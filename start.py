#!/usr/bin/env python3
"""Command-line entry point for LinkedIn outreach workflows."""
import argparse
import sys

from linkedin_outreach.supervisor import run_supervised


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="LinkedIn outreach automation")
    parser.add_argument(
        "command",
        choices=("message", "add-contact"),
        help="Send messages to existing contacts or add contacts from people search.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    return run_supervised(args.command)


if __name__ == "__main__":
    sys.exit(main())
