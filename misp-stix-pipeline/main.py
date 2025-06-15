"""Main entry point for the MISP-STIX pipeline."""

import argparse
import logging
import sys
from datetime import datetime, timedelta

from .core.orchestrator import PipelineOrchestrator
from .utils.exceptions import MISPPipelineError
from .utils.logging import setup_logger

logger = setup_logger(__name__)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="MISP-STIX Processing Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process events from last 24 hours
  python -m misp_stix_pipeline

  # Process events from last 7 days
  python -m misp_stix_pipeline --days 7

  # Process events from specific date
  python -m misp_stix_pipeline --since 2024-01-01
        """,
    )

    parser.add_argument(
        "--days", type=int, default=1, help="Process events from last N days (default: 1)"
    )

    parser.add_argument("--since", type=str, help="Process events since this date (YYYY-MM-DD)")

    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    return parser.parse_args()


def main() -> int:
    """
    Main entry point for the pipeline.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    args = parse_arguments()

    # Configure logging
    if args.verbose:
        setup_logger("misp_stix_pipeline", level=logging.DEBUG)

    try:
        # Determine timestamp
        if args.since:
            since = datetime.strptime(args.since, "%Y-%m-%d")
        else:
            since = datetime.now() - timedelta(days=args.days)

        logger.info(f"Starting pipeline for events since {since}")

        # Initialize and run pipeline
        orchestrator = PipelineOrchestrator()
        summary = orchestrator.run(since=since)

        # Print summary
        print("\nPipeline Execution Summary:")
        print(f"- Total events in MISP: {summary['total_events']}")
        print(f"- Events processed: {summary['processed_events']}")
        print(f"- Events failed: {summary['failed_events']}")
        print(f"- Messages sent to SQS: {summary['messages_sent']}")
        print(f"- Execution time: {summary['duration_seconds']:.2f} seconds")

        if summary["failed_events"] > 0:
            print(f"\nWarning: {summary['failed_events']} events failed to process")
            return 1

        return 0

    except MISPPipelineError as e:
        logger.error(f"Pipeline error: {e}")
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        print("\nPipeline interrupted", file=sys.stderr)
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

# Example usage script
"""
# example_usage.py
#!/usr/bin/env python3

'''Example usage of the MISP-STIX Pipeline Framework'''

from datetime import datetime, timedelta
from misp_stix_pipeline.core.orchestrator import PipelineOrchestrator

def example_basic_usage():
    '''Basic usage example'''
    # Initialize the orchestrator
    orchestrator = PipelineOrchestrator()

    # Run pipeline for last 24 hours
    summary = orchestrator.run()

    print(f"Processed {summary['processed_events']} events")

def example_custom_timeframe():
    '''Example with custom timeframe'''
    orchestrator = PipelineOrchestrator()

    # Process events from last week
    since = datetime.now() - timedelta(days=7)
    summary = orchestrator.run(since=since)

    print(f"Processed {summary['processed_events']} events from last week")

def example_direct_component_usage():
    '''Example of using individual components'''
    from misp_stix_pipeline.config import ConfigManager
    from misp_stix_pipeline.core.misp_client import MISPClient
    from misp_stix_pipeline.core.stix_converter import STIXConverter

    # Get configuration
    config = ConfigManager()
    config_data = config.get_config()

    # Initialize MISP client
    misp_client = MISPClient(
        url=config_data['misp_url'],
        api_key=config_data['misp_api_key']
    )

    # Get event count
    count = misp_client.get_event_count()
    print(f"Total events: {count}")

    # Fetch and convert a single event
    events = misp_client.get_new_events(limit=1)
    if events:
        converter = STIXConverter()
        stix_data = converter.process(events[0])
        print(f"Converted event {stix_data['event_uuid']} to STIX")

if __name__ == "__main__":
    # Run examples
    print("Running basic usage example...")
    example_basic_usage()

    print("\nRunning custom timeframe example...")
    example_custom_timeframe()

    print("\nRunning direct component usage example...")
    example_direct_component_usage()
"""
