"""
Akash Network Core Library

Provides programmatic access to Akash Network deployment operations.
All functions return dicts instead of printing to stdout.
"""

import os
import time
import httpx
from typing import Any

# Module-level httpx client for connection reuse (thread-safe)
_http_client = httpx.Client(timeout=30.0)


# Constants
API_BASE_URL = "https://console-api.akash.network"
API_KEY_ENV = "AKASH_API_KEY"
DEFAULT_DEPOSIT = 5
DEFAULT_CPU = "0.5"
DEFAULT_MEMORY = "512Mi"
DEFAULT_STORAGE = "512Mi"
MAX_BID_RETRIES = 5  # Reduced from 10 for faster demos
BID_POLL_DELAY = 2  # Reduced from 3 for faster demos
PRICING_DENOM = "ibc/170C677610AC31DF0904FFE09CD3B5C657492170E7E52372E48756B71E56F2F1"
PRICING_AMOUNT = 10000


class AkashError(Exception):
    """Base exception for Akash-related errors."""
    pass


def _get_api_key() -> str:
    """Get API key from environment variable.

    Raises:
        ValueError: If AKASH_API_KEY is not set
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise ValueError(f"{API_KEY_ENV} environment variable not set")
    return api_key


def _api_request(method: str, endpoint: str, data: dict[str, Any] | None = None,
                 api_key: str | None = None) -> dict[str, Any]:
    """
    Make an HTTP request to the Akash Console API.

    Args:
        method: HTTP method (GET, POST, DELETE)
        endpoint: API endpoint path (e.g., /v1/deployments)
        data: Optional dict to JSON-encode for POST requests
        api_key: API key for authentication

    Returns:
        Parsed JSON response

    Raises:
        AkashError: For HTTP or network errors
    """
    if api_key is None:
        api_key = _get_api_key()

    url = f"{API_BASE_URL}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "User-Agent": "akash-core/1.0",
    }

    try:
        import time as time_lib
        start = time_lib.time()
        if method == "GET":
            response = _http_client.get(url, headers=headers)
        elif method == "POST":
            response = _http_client.post(url, json=data, headers=headers)
        elif method == "DELETE":
            response = _http_client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        elapsed = time_lib.time() - start
        print(f"[TIMING] Akash API {method} {endpoint} took: {elapsed:.3f}s")

        response.raise_for_status()
        return response.json() if response.content else {}

    except httpx.HTTPStatusError as e:
        error_body = e.response.text
        raise AkashError(f"API Error ({e.response.status_code}): {error_body}") from e
    except httpx.RequestError as e:
        raise AkashError(f"Network Error: {e}") from e


def _generate_sdl(image: str, port: int, env_vars: list[str] | None = None,
                  cpu: str = DEFAULT_CPU, memory: str = DEFAULT_MEMORY,
                  storage: str = DEFAULT_STORAGE) -> str:
    """
    Generate an SDL (deployment spec) from a template.

    Args:
        image: Docker image name (e.g., nginx:latest)
        port: Container port to expose
        env_vars: Optional list of env var strings (KEY=VALUE)
        cpu: CPU units
        memory: Memory size (e.g., 512Mi)
        storage: Storage size (e.g., 512Mi)

    Returns:
        SDL YAML string
    """
    sdl = f"""version: "2.0"
services:
  web:
    image: {image}
    expose:
      - port: {port}
        as: 80
        to:
          - global: true
"""

    # Add environment variables if provided
    if env_vars:
        sdl += "    env:\n"
        for env_var in env_vars:
            sdl += f"      - {env_var}\n"

    sdl += f"""profiles:
  compute:
    web:
      resources:
        cpu:
          units: {cpu}
        memory:
          size: {memory}
        storage:
          - size: {storage}
  placement:
    dcloud:
      pricing:
        web:
          denom: {PRICING_DENOM}
          amount: {PRICING_AMOUNT}
deployment:
  web:
    dcloud:
      profile: web
      count: 1
"""
    return sdl


def _create_deployment(sdl: str, deposit: float = DEFAULT_DEPOSIT,
                       api_key: str | None = None) -> tuple[str, str]:
    """
    Create a deployment on Akash Network.

    Args:
        sdl: SDL YAML string
        deposit: Deposit amount in USD
        api_key: API key for authentication

    Returns:
        Tuple of (dseq, manifest)

    Raises:
        AkashError: If deployment creation fails
    """
    data = {
        "data": {
            "sdl": sdl,
            "deposit": deposit
        }
    }
    response = _api_request("POST", "/v1/deployments", data=data, api_key=api_key)
    dseq = response["data"]["dseq"]
    manifest = response["data"]["manifest"]
    return dseq, manifest


def _get_bids(dseq: str, max_retries: int = MAX_BID_RETRIES, delay: int = BID_POLL_DELAY,
              api_key: str | None = None) -> list[dict[str, Any]]:
    """
    Poll for bids on a deployment.

    Args:
        dseq: Deployment sequence ID
        max_retries: Maximum number of polling attempts
        delay: Delay between attempts in seconds
        api_key: API key for authentication

    Returns:
        List of open bid responses

    Raises:
        AkashError: If no bids received after max retries
    """
    for attempt in range(max_retries):
        response = _api_request("GET", f"/v1/bids?dseq={dseq}", api_key=api_key)
        bids = response.get("data", [])

        if bids:
            # Filter for open bids
            open_bids = [
                bid for bid in bids
                if bid.get("bid", {}).get("state") == "open"
            ]
            if open_bids:
                return open_bids

        if attempt < max_retries - 1:
            time.sleep(delay)

    raise AkashError(f"No bids received after {max_retries} attempts")


def _pick_cheapest_bid(bids: list[dict[str, Any]]) -> tuple[dict[str, Any], float]:
    """
    Pick the cheapest bid from a list of bids.

    Args:
        bids: List of bid responses

    Returns:
        Tuple of (bid_response, price_as_float)

    Raises:
        AkashError: If no valid bids found
    """
    cheapest = None
    cheapest_price = float('inf')

    for bid_response in bids:
        bid = bid_response.get("bid", {})
        price_str = bid.get("price", {}).get("amount", "0")
        try:
            price = float(price_str)
        except ValueError:
            continue

        if price < cheapest_price:
            cheapest_price = price
            cheapest = bid_response

    if cheapest is None:
        raise AkashError("No valid bids found")

    return cheapest, cheapest_price


def _create_lease(manifest: str, dseq: str, gseq: int, oseq: int,
                  provider: str, api_key: str | None = None) -> dict[str, Any]:
    """
    Create a lease (accept a bid) on Akash Network.

    Args:
        manifest: Deployment manifest string
        dseq: Deployment sequence ID
        gseq: Group sequence ID from bid
        oseq: Order sequence ID from bid
        provider: Provider address from bid
        api_key: API key for authentication

    Returns:
        Lease creation response
    """
    data = {
        "manifest": manifest,
        "leases": [
            {
                "dseq": dseq,
                "gseq": gseq,
                "oseq": oseq,
                "provider": provider
            }
        ]
    }
    return _api_request("POST", "/v1/leases", data=data, api_key=api_key)


def _extract_deployment_url(lease_response: dict[str, Any],
                            service_name: str = "web") -> str | None:
    """
    Extract the deployment URL from the lease creation response.

    Args:
        lease_response: Response from lease creation
        service_name: Name of the service (default: "web")

    Returns:
        URL string or None if not found
    """
    try:
        leases = lease_response.get("data", {}).get("leases", [])
        if not leases:
            return None

        lease = leases[0]
        status = lease.get("status")
        if not status:
            return None

        services = status.get("services", {})
        service = services.get(service_name)
        if not service:
            return None

        uris = service.get("uris", [])
        if uris:
            return uris[0]
    except (KeyError, IndexError, TypeError):
        pass

    return None


def deploy(image: str, port: int, env: list[str] | None = None,
           cpu: str = DEFAULT_CPU, memory: str = DEFAULT_MEMORY,
           storage: str = DEFAULT_STORAGE) -> dict[str, Any]:
    """
    Deploy a Docker image to Akash Network.

    Args:
        image: Docker image name (e.g., nginx:latest)
        port: Container port to expose
        env: Optional list of environment variable strings (KEY=VALUE)
        cpu: CPU units (default: "0.5")
        memory: Memory size (default: "512Mi")
        storage: Storage size (default: "512Mi")

    Returns:
        dict with keys:
            - success (bool): True if deployment succeeded
            - dseq (str | None): Deployment sequence ID
            - provider (str | None): Provider address
            - url (str | None): Deployment URL
            - price (float | None): Bid price
            - error (str | None): Error message if failed
    """
    try:
        api_key = _get_api_key()

        # Generate SDL
        sdl = _generate_sdl(image, port, env, cpu, memory, storage)

        # Create deployment
        dseq, manifest = _create_deployment(sdl, DEFAULT_DEPOSIT, api_key)

        # Wait for bids
        bids = _get_bids(dseq, api_key=api_key)

        # Pick cheapest bid
        bid_response, price = _pick_cheapest_bid(bids)
        bid = bid_response.get("bid", {})
        provider = bid.get("id", {}).get("provider", "unknown")
        gseq = bid.get("id", {}).get("gseq", 1)
        oseq = bid.get("id", {}).get("oseq", 1)

        # Create lease
        lease_response = _create_lease(manifest, dseq, gseq, oseq, provider, api_key)

        # Extract URL
        url = _extract_deployment_url(lease_response)

        return {
            "success": True,
            "dseq": dseq,
            "provider": provider,
            "url": url,
            "price": price,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "dseq": None,
            "provider": None,
            "url": None,
            "price": None,
            "error": str(e)
        }


def list_deployments() -> dict[str, Any]:
    """
    List all active deployments.

    Returns:
        dict with keys:
            - success (bool): True if request succeeded
            - deployments (list): List of deployment dicts with dseq, state, created_at
            - error (str | None): Error message if failed
    """
    try:
        api_key = _get_api_key()
        response = _api_request("GET", "/v1/deployments", api_key=api_key)
        deployments_raw = response.get("data", {}).get("deployments", [])

        deployments = []
        for deployment_wrapper in deployments_raw:
            deployment = deployment_wrapper.get("deployment", {})
            deployments.append({
                "dseq": deployment.get("id", {}).get("dseq", "unknown"),
                "state": deployment.get("state", "unknown"),
                "created_at": deployment.get("created_at", "unknown")
            })

        return {
            "success": True,
            "deployments": deployments,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "deployments": [],
            "error": str(e)
        }


def close_deployment(dseq: str) -> dict[str, Any]:
    """
    Close a deployment and recover remaining deposit.

    Args:
        dseq: Deployment sequence ID

    Returns:
        dict with keys:
            - success (bool): True if deployment closed successfully
            - dseq (str): The dseq that was closed
            - error (str | None): Error message if failed
    """
    try:
        api_key = _get_api_key()
        response = _api_request("DELETE", f"/v1/deployments/{dseq}", api_key=api_key)

        success = response.get("data", {}).get("success", False)

        return {
            "success": success,
            "dseq": dseq,
            "error": None if success else "Failed to close deployment"
        }

    except Exception as e:
        return {
            "success": False,
            "dseq": dseq,
            "error": str(e)
        }
