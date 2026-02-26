#!/usr/bin/env python3
"""
Akash Network Deployment CLI
Deploys Docker containers to Akash Network using the Console Managed Wallet API.

Usage:
    python deploy.py deploy <image> --port <port> [--env KEY=VAL] [--cpu N] [--memory N] [--storage N]
    python deploy.py list
    python deploy.py close <dseq>
"""

import argparse
import sys

from akash_core import (
    deploy as deploy_core,
    list_deployments as list_deployments_core,
    close_deployment as close_deployment_core,
    AkashError,
    DEFAULT_CPU,
    DEFAULT_MEMORY,
    DEFAULT_STORAGE,
    DEFAULT_DEPOSIT
)


def deploy_image(image, port, env_vars=None, cpu=DEFAULT_CPU, memory=DEFAULT_MEMORY,
                 storage=DEFAULT_STORAGE, deposit=DEFAULT_DEPOSIT):
    """
    Deploy a Docker image to Akash Network.

    Args:
        image: Docker image name
        port: Container port
        env_vars: Optional list of environment variable strings
        cpu: CPU units
        memory: Memory size
        storage: Storage size
        deposit: Deposit amount in USD
    """
    print("Generating SDL...")

    result = deploy_core(
        image=image,
        port=port,
        env=env_vars,
        cpu=cpu,
        memory=memory,
        storage=storage
    )

    if not result["success"]:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    dseq = result["dseq"]
    provider = result["provider"]
    price = result["price"]
    url = result["url"]

    print(f"Deployment created! DSEQ: {dseq}")
    print(f"\nWaiting for provider bids...")
    print(f"Selected provider: {provider}")
    print(f"Price: {price}")
    print("Lease created successfully!")

    if url:
        print("\n" + "="*50)
        print("Deployment is LIVE!")
        print("="*50)
        print(f"URL: {url}")
        print(f"DSEQ: {dseq}")
        print("="*50)
        print("\nTo close this deployment later:")
        print(f"  python deploy.py close {dseq}")
    else:
        print("\nLease created, but URL not available yet.")
        print("The deployment may still be starting up.")
        print(f"DSEQ: {dseq}")
        print("\nYou can check status later:")
        print(f"  python deploy.py list")


def list_deployments():
    """List all active deployments."""
    print("Fetching deployments...")

    result = list_deployments_core()

    if not result["success"]:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    deployments = result["deployments"]

    if not deployments:
        print("No active deployments found.")
        return

    print(f"\nFound {len(deployments)} deployment(s):\n")
    for deployment in deployments:
        dseq = deployment["dseq"]
        state = deployment["state"]
        created = deployment["created_at"]
        print(f"DSEQ: {dseq}")
        print(f"  State: {state}")
        print(f"  Created: {created}")
        print()


def close_deployment(dseq):
    """
    Close a deployment and recover remaining deposit.

    Args:
        dseq: Deployment sequence ID
    """
    print(f"Closing deployment {dseq}...")

    result = close_deployment_core(dseq)

    if not result["success"]:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Deployment {dseq} closed successfully.")
    print("Remaining deposit will be refunded to your account.")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Deploy Docker containers to Akash Network",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy nginx
  python deploy.py deploy nginx:latest --port 80

  # Deploy with environment variables
  python deploy.py deploy myapp:v1 --port 3000 --env "API_KEY=secret" --env "DB_HOST=localhost"

  # Deploy with custom resources
  python deploy.py deploy pytorch/pytorch:latest --port 8080 --cpu 2 --memory 2Gi --storage 5Gi

  # List active deployments
  python deploy.py list

  # Close a deployment
  python deploy.py close 25690382

Environment Variables:
  AKASH_API_KEY  Your Akash Console API key (required)
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Deploy a Docker image")
    deploy_parser.add_argument("image", help="Docker image name (e.g., nginx:latest)")
    deploy_parser.add_argument("--port", type=int, required=True, help="Container port to expose")
    deploy_parser.add_argument("--env", action="append", help="Environment variables (KEY=VALUE, can be used multiple times)")
    deploy_parser.add_argument("--cpu", type=str, default=DEFAULT_CPU, help=f"CPU units (default: {DEFAULT_CPU})")
    deploy_parser.add_argument("--memory", default=DEFAULT_MEMORY, help=f"Memory size (default: {DEFAULT_MEMORY})")
    deploy_parser.add_argument("--storage", default=DEFAULT_STORAGE, help=f"Storage size (default: {DEFAULT_STORAGE})")
    deploy_parser.add_argument("--deposit", type=float, default=DEFAULT_DEPOSIT, help=f"Deposit amount in USD (default: ${DEFAULT_DEPOSIT})")

    # List command
    subparsers.add_parser("list", help="List active deployments")

    # Close command
    close_parser = subparsers.add_parser("close", help="Close a deployment")
    close_parser.add_argument("dseq", help="Deployment sequence ID to close")

    args = parser.parse_args()

    if args.command == "deploy":
        deploy_image(
            args.image,
            args.port,
            env_vars=args.env,
            cpu=args.cpu,
            memory=args.memory,
            storage=args.storage,
            deposit=args.deposit
        )
    elif args.command == "list":
        list_deployments()
    elif args.command == "close":
        close_deployment(args.dseq)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
