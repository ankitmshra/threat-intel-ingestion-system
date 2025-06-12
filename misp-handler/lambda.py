import json
import logging
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, TypedDict

import boto3
from mypy_boto3_lambda import LambdaClient
from mypy_boto3_sqs import SQSClient

from .config import MispConfig as MC
from .logging import CloudWatchFormatter
from .misp import MispThreatFeedProcessor


@dataclass
class BatchJob:
    """Represents a batch processing job"""

    job_id: str
    page: int
    batch_size: int
    function_name: str
    payload: Dict[str, Any]
    created_at: str


@dataclass
class JobResult:
    """Result of a batch job execution"""

    job_id: str
    status: str  # "success", "failed", "timeout"
    page: int
    batch_size: int
    processed_events: int = 0
    error_message: Optional[str] = None
    execution_time: float = 0.0
    retry_count: int = 0


@dataclass
class OrchestrationResult:
    """Overall result of orchestration"""

    status: str  # "completed", "partial_success", "critical_failure"
    total_events: int
    instances_spawned: int
    successful_jobs: int
    failed_jobs: int
    warning_threshold_exceeded: bool
    halt_threshold_exceeded: bool
    failure_rate: float
    total_execution_time: float
    request_id: str
    job_results: List[JobResult]


class BatchJobDict(TypedDict):
    """Typed dictionary for serialized BatchJob"""

    job_id: str
    page: int
    batch_size: int
    function_name: str
    payload: Dict[str, Any]
    created_at: str


class JobResultDict(TypedDict):
    """Typed dictionary for serialized JobResult"""

    job_id: str
    status: str
    page: int
    batch_size: int
    processed_events: int
    error_message: Optional[str]
    execution_time: float
    retry_count: int


class RetryMessage(TypedDict):
    """Typed dictionary for retry queue message"""

    original_job: BatchJobDict
    failure_result: JobResultDict
    retry_timestamp: str
    request_id: str
    failure_reason: Optional[str]


class LambdaOrchestrator:
    """
    Orchestrates parallel processing of MISP events using multiple Lambda instances.
    Waits for all jobs to complete and handles failures with deferred retry.
    """

    def __init__(self, request_id: Optional[str] = None):
        self.request_id: str = request_id or str(uuid.uuid4())

        # Failure thresholds
        self.WARNING_THRESHOLD: float = 0.20  # 20%
        self.HALT_THRESHOLD: float = 0.50  # 50%

        # Setup Logger
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
            handler = logging.StreamHandler()
            handler.setFormatter(CloudWatchFormatter())
            self.logger.addHandler(handler)

        # Load configuration and clients
        try:
            self.config: MC = MC()
            self.lambda_client: LambdaClient = boto3.client("lambda")  # type: ignore[misc]
            self.sqs_client: SQSClient = boto3.client("sqs")  # type: ignore[misc]
            self.processor = MispThreatFeedProcessor(request_id=self.request_id)

            self.logger.info(
                "Lambda Orchestrator initialized successfully",
                extra={
                    "extra_fields": {
                        "request_id": self.request_id,
                        "max_events_per_instance": self.config.max_events_per_instance,
                        "max_concurrent_instances": self.config.max_concurrent_instances,
                        "warning_threshold": self.WARNING_THRESHOLD,
                        "halt_threshold": self.HALT_THRESHOLD,
                    }
                },
            )
        except Exception as e:
            self.logger.critical(
                f"Failed to initialize Lambda Orchestrator: {str(e)}",
                extra={"extra_fields": {"request_id": self.request_id}},
                exc_info=True,
            )
            raise

    def calculate_batch_distribution(self, total_events: int) -> List[BatchJob]:
        """
        Calculate how to distribute events across Lambda instances using page-based pagination.

        Args:
            total_events: Total number of events to process

        Returns:
            List of BatchJob objects representing the work distribution
        """
        max_events_per_instance = self.config.max_events_per_instance
        max_concurrent_instances = self.config.max_concurrent_instances

        # Calculate how many pages we need
        total_pages_needed = (total_events + max_events_per_instance - 1) // max_events_per_instance

        # Limit by max concurrent instances
        actual_instances = min(total_pages_needed, max_concurrent_instances)

        batch_jobs: List[BatchJob] = []

        for page in range(1, actual_instances + 1):  # MISP uses 1-indexed pages
            job = BatchJob(
                job_id=str(uuid.uuid4()),
                page=page,
                batch_size=max_events_per_instance,
                function_name=self.config.batch_processing_function_name,
                payload={
                    "action": "process_batch",
                    "page": page,
                    "batch_size": max_events_per_instance,
                    "request_id": self.request_id,
                    "job_id": str(uuid.uuid4()),
                },
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            batch_jobs.append(job)

        self.logger.info(
            f"Calculated batch distribution for {total_events} events",
            extra={
                "extra_fields": {
                    "total_events": total_events,
                    "total_pages_needed": total_pages_needed,
                    "actual_instances": actual_instances,
                    "max_events_per_instance": max_events_per_instance,
                    "request_id": self.request_id,
                }
            },
        )

        return batch_jobs

    def invoke_lambda_sync(self, batch_job: BatchJob, timeout: int = 900) -> JobResult:
        """
        Invoke a Lambda function synchronously and wait for completion.

        Args:
            batch_job: The batch job to execute
            timeout: Maximum time to wait for completion (seconds)

        Returns:
            JobResult with execution details

        Raises:
            Exception: If Lambda invocation fails
        """
        start_time = time.time()

        try:
            self.logger.info(
                f"Invoking Lambda for batch job {batch_job.job_id}",
                extra={
                    "extra_fields": {
                        "job_id": batch_job.job_id,
                        "page": batch_job.page,
                        "batch_size": batch_job.batch_size,
                        "function_name": batch_job.function_name,
                        "request_id": self.request_id,
                    }
                },
            )

            response = self.lambda_client.invoke(
                FunctionName=batch_job.function_name,
                InvocationType="RequestResponse",  # Synchronous invocation
                Payload=json.dumps(batch_job.payload),
            )

            execution_time = time.time() - start_time
            status_code = response.get("StatusCode", 0)

            # Check for Lambda execution errors
            if status_code != 200:
                error_msg = f"Lambda returned status code {status_code}"
                self.logger.error(
                    f"Lambda invocation failed for job {batch_job.job_id}: {error_msg}",
                    extra={
                        "extra_fields": {
                            "job_id": batch_job.job_id,
                            "status_code": status_code,
                            "execution_time": execution_time,
                            "request_id": self.request_id,
                        }
                    },
                )
                return JobResult(
                    job_id=batch_job.job_id,
                    status="failed",
                    page=batch_job.page,
                    batch_size=batch_job.batch_size,
                    error_message=error_msg,
                    execution_time=execution_time,
                )

            # Check for function errors in response
            if "FunctionError" in response:
                error_payload = response.get("Payload", b"").read().decode("utf-8")
                error_msg = f"Lambda function error: {error_payload}"
                self.logger.error(
                    f"Lambda function error for job {batch_job.job_id}: {error_msg}",
                    extra={
                        "extra_fields": {
                            "job_id": batch_job.job_id,
                            "function_error": response["FunctionError"],
                            "error_payload": error_payload,
                            "execution_time": execution_time,
                            "request_id": self.request_id,
                        }
                    },
                )
                return JobResult(
                    job_id=batch_job.job_id,
                    status="failed",
                    page=batch_job.page,
                    batch_size=batch_job.batch_size,
                    error_message=error_msg,
                    execution_time=execution_time,
                )

            # Success case
            self.logger.info(
                f"Successfully completed batch job {batch_job.job_id}",
                extra={
                    "extra_fields": {
                        "job_id": batch_job.job_id,
                        "page": batch_job.page,
                        "batch_size": batch_job.batch_size,
                        "status_code": status_code,
                        "execution_time": execution_time,
                        "request_id": self.request_id,
                    }
                },
            )

            return JobResult(
                job_id=batch_job.job_id,
                status="success",
                page=batch_job.page,
                batch_size=batch_job.batch_size,
                processed_events=batch_job.batch_size,  # Assume full processing on success
                execution_time=execution_time,
            )

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = str(e)

            self.logger.error(
                f"Failed to invoke Lambda for batch job {batch_job.job_id}: {error_msg}",
                extra={
                    "extra_fields": {
                        "job_id": batch_job.job_id,
                        "page": batch_job.page,
                        "batch_size": batch_job.batch_size,
                        "execution_time": execution_time,
                        "request_id": self.request_id,
                    }
                },
                exc_info=True,
            )

            return JobResult(
                job_id=batch_job.job_id,
                status="failed",
                page=batch_job.page,
                batch_size=batch_job.batch_size,
                error_message=error_msg,
                execution_time=execution_time,
            )

    def send_failed_job_to_retry_queue(self, batch_job: BatchJob, job_result: JobResult) -> None:
        """
        Send failed job to retry queue for later processing.

        Args:
            batch_job: Original batch job that failed
            job_result: Result containing failure details
        """
        try:
            # Create properly typed retry message
            retry_message: RetryMessage = {
                "original_job": BatchJobDict(
                    job_id=batch_job.job_id,
                    page=batch_job.page,
                    batch_size=batch_job.batch_size,
                    function_name=batch_job.function_name,
                    payload=batch_job.payload,
                    created_at=batch_job.created_at,
                ),
                "failure_result": JobResultDict(
                    job_id=job_result.job_id,
                    status=job_result.status,
                    page=job_result.page,
                    batch_size=job_result.batch_size,
                    processed_events=job_result.processed_events,
                    error_message=job_result.error_message,
                    execution_time=job_result.execution_time,
                    retry_count=job_result.retry_count,
                ),
                "retry_timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": self.request_id,
                "failure_reason": job_result.error_message,
            }

            self.sqs_client.send_message(
                QueueUrl=self.config.retry_queue_url,
                MessageBody=json.dumps(retry_message),
                MessageAttributes={
                    "JobId": {"StringValue": batch_job.job_id, "DataType": "String"},
                    "Page": {"StringValue": str(batch_job.page), "DataType": "String"},
                    "RequestId": {"StringValue": self.request_id, "DataType": "String"},
                },
            )

            self.logger.info(
                f"Sent failed job {batch_job.job_id} to retry queue",
                extra={
                    "extra_fields": {
                        "job_id": batch_job.job_id,
                        "page": batch_job.page,
                        "retry_queue_url": self.config.retry_queue_url,
                        "request_id": self.request_id,
                    }
                },
            )

        except Exception as e:
            self.logger.error(
                f"Failed to send job {batch_job.job_id} to retry queue: {str(e)}",
                extra={
                    "extra_fields": {
                        "job_id": batch_job.job_id,
                        "page": batch_job.page,
                        "request_id": self.request_id,
                    }
                },
                exc_info=True,
            )

    def evaluate_results(
        self, job_results: List[JobResult], total_execution_time: float
    ) -> OrchestrationResult:
        """
        Evaluate the overall success/failure of the batch processing operation.

        Args:
            job_results: List of individual job results
            total_execution_time: Total time for the orchestration

        Returns:
            OrchestrationResult with overall assessment
        """
        total_jobs = len(job_results)
        successful_jobs = [r for r in job_results if r.status == "success"]
        failed_jobs = [r for r in job_results if r.status == "failed"]

        failure_rate = len(failed_jobs) / total_jobs if total_jobs > 0 else 0.0

        warning_threshold_exceeded = failure_rate > self.WARNING_THRESHOLD
        halt_threshold_exceeded = failure_rate > self.HALT_THRESHOLD

        # Determine overall status
        if failure_rate == 0.0:
            status = "completed"
        elif halt_threshold_exceeded:
            status = "critical_failure"
        else:
            status = "partial_success"

        total_events = sum(r.processed_events for r in successful_jobs)

        result = OrchestrationResult(
            status=status,
            total_events=total_events,
            instances_spawned=total_jobs,
            successful_jobs=len(successful_jobs),
            failed_jobs=len(failed_jobs),
            warning_threshold_exceeded=warning_threshold_exceeded,
            halt_threshold_exceeded=halt_threshold_exceeded,
            failure_rate=failure_rate,
            total_execution_time=total_execution_time,
            request_id=self.request_id,
            job_results=job_results,
        )

        self.logger.info(
            f"Orchestration evaluation completed: {status}",
            extra={
                "extra_fields": {
                    "status": status,
                    "total_jobs": total_jobs,
                    "successful_jobs": len(successful_jobs),
                    "failed_jobs": len(failed_jobs),
                    "failure_rate": failure_rate,
                    "warning_threshold_exceeded": warning_threshold_exceeded,
                    "halt_threshold_exceeded": halt_threshold_exceeded,
                    "total_execution_time": total_execution_time,
                    "request_id": self.request_id,
                }
            },
        )

        return result

    def orchestrate_parallel_processing(self) -> Dict[str, Any]:
        """
        Orchestrate the parallel processing of all available MISP events.
        Waits for all jobs to complete before returning.

        Returns:
            Dictionary containing orchestration results and metadata

        Raises:
            Exception: If orchestration fails critically
        """
        orchestration_start_time = time.time()

        try:
            # Get total count of events to process
            total_events = self.processor.get_events_count_since_last_sync()

            if total_events == 0:
                self.logger.info(
                    "No events to process", extra={"extra_fields": {"request_id": self.request_id}}
                )
                return {
                    "status": "completed",
                    "total_events": 0,
                    "instances_spawned": 0,
                    "message": "No events to process",
                    "request_id": self.request_id,
                }

            # Calculate batch distribution
            batch_jobs = self.calculate_batch_distribution(total_events)

            # Execute all jobs concurrently and wait for completion
            job_results: List[JobResult] = []

            with ThreadPoolExecutor(max_workers=len(batch_jobs)) as executor:
                # Submit all jobs
                future_to_job: Dict[Future[JobResult], BatchJob] = {
                    executor.submit(self.invoke_lambda_sync, job): job for job in batch_jobs
                }

                # Collect results as they complete
                for future in as_completed(future_to_job):
                    batch_job = future_to_job[future]
                    try:
                        job_result = future.result()
                        job_results.append(job_result)

                        # Send failed jobs to retry queue
                        if job_result.status == "failed":
                            self.send_failed_job_to_retry_queue(batch_job, job_result)

                    except Exception as e:
                        # This shouldn't happen as invoke_lambda_sync handles its own exceptions
                        error_result = JobResult(
                            job_id=batch_job.job_id,
                            status="failed",
                            page=batch_job.page,
                            batch_size=batch_job.batch_size,
                            error_message=f"Unexpected error: {str(e)}",
                        )
                        job_results.append(error_result)
                        self.send_failed_job_to_retry_queue(batch_job, error_result)

                        self.logger.error(
                            f"Unexpected error processing job {batch_job.job_id}: {str(e)}",
                            extra={
                                "extra_fields": {
                                    "job_id": batch_job.job_id,
                                    "request_id": self.request_id,
                                }
                            },
                            exc_info=True,
                        )

            # Calculate total execution time
            total_execution_time = time.time() - orchestration_start_time

            # Evaluate overall results
            orchestration_result = self.evaluate_results(job_results, total_execution_time)

            # Log final results
            self.logger.info(
                f"Orchestration completed: {orchestration_result.status}",
                extra={
                    "extra_fields": {
                        "total_events_available": total_events,
                        "total_events_processed": orchestration_result.total_events,
                        "instances_spawned": orchestration_result.instances_spawned,
                        "successful_jobs": orchestration_result.successful_jobs,
                        "failed_jobs": orchestration_result.failed_jobs,
                        "failure_rate": orchestration_result.failure_rate,
                        "total_execution_time": total_execution_time,
                        "status": orchestration_result.status,
                        "request_id": self.request_id,
                    }
                },
            )

            # Return simplified result for API response
            return {
                "status": orchestration_result.status,
                "total_events_available": total_events,
                "total_events_processed": orchestration_result.total_events,
                "instances_spawned": orchestration_result.instances_spawned,
                "successful_jobs": orchestration_result.successful_jobs,
                "failed_jobs": orchestration_result.failed_jobs,
                "failure_rate": orchestration_result.failure_rate,
                "warning_threshold_exceeded": orchestration_result.warning_threshold_exceeded,
                "halt_threshold_exceeded": orchestration_result.halt_threshold_exceeded,
                "total_execution_time": total_execution_time,
                "request_id": self.request_id,
                "message": self._get_status_message(orchestration_result),
            }

        except Exception as e:
            total_execution_time = time.time() - orchestration_start_time
            self.logger.error(
                f"Orchestration failed: {str(e)}",
                extra={
                    "extra_fields": {
                        "total_execution_time": total_execution_time,
                        "request_id": self.request_id,
                    }
                },
                exc_info=True,
            )
            raise

    def _get_status_message(self, result: OrchestrationResult) -> str:
        """Generate a human-readable status message based on results"""
        if result.status == "completed":
            return (
                f"Successfully processed all {result.total_events} "
                f"events across {result.instances_spawned} instances"
            )
        elif result.status == "partial_success":
            return (
                f"Processed {result.total_events} events with {result.failed_jobs} failures "
                f"({result.failure_rate:.1%} failure rate). Failed jobs sent to retry queue."
            )
        else:  # critical_failure
            return (
                f"Critical failure: {result.failure_rate:.1%} failure rate exceeds halt "
                f"threshold of {self.HALT_THRESHOLD:.1%}. {result.failed_jobs} "
                f"jobs sent to retry queue for manual intervention."
            )
