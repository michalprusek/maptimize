"""SSRF protection for outbound URL fetches.

``_is_safe_url`` used to live in ``services.gemini_agent_service``; it was moved
here so paper discovery (and anything else that fetches a user- or
publisher-supplied URL) does not have to import the whole agent service just to
reach the guard. The agent has since been removed, but the guard lives on.
"""
import ipaddress
from urllib.parse import urlparse


def _is_safe_url(url: str) -> tuple[bool, str]:
    """
    Validate URL for SSRF protection.

    Blocks:
    - Non HTTP/HTTPS schemes
    - Private/internal IP addresses (including via DNS resolution)
    - Cloud metadata endpoints
    - Localhost and loopback addresses

    Returns:
        Tuple of (is_safe, error_message)
    """
    import socket

    def _check_ip_is_safe(ip_str: str) -> tuple[bool, str]:
        """Check if an IP address is safe (not private/internal)."""
        try:
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private:
                return False, f"Access to private IP address ({ip_str}) is not allowed"
            if ip.is_loopback:
                return False, f"Access to loopback address ({ip_str}) is not allowed"
            if ip.is_link_local:
                return False, f"Access to link-local address ({ip_str}) is not allowed"
            if ip.is_multicast:
                return False, f"Access to multicast address ({ip_str}) is not allowed"
            # Block cloud metadata endpoints
            if str(ip) in ("169.254.169.254", "100.100.100.200"):
                return False, "Access to cloud metadata endpoints is not allowed"
            return True, ""
        except ValueError:
            return False, f"Invalid IP address: {ip_str}"

    try:
        parsed = urlparse(url)

        # Only allow http/https
        if parsed.scheme not in ("http", "https"):
            return False, f"Only HTTP/HTTPS URLs allowed, got: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return False, "Invalid URL: no hostname"

        # Block localhost and loopback (string checks)
        if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"):
            return False, "Access to localhost is not allowed"

        # Check for obvious internal hostnames
        hostname_lower = hostname.lower()
        if hostname_lower.endswith(".internal") or hostname_lower.endswith(".local"):
            return False, "Access to internal hostnames is not allowed"

        # Try to parse as IP address first
        try:
            ip = ipaddress.ip_address(hostname)
            return _check_ip_is_safe(str(ip))
        except ValueError:
            # It's a hostname, not an IP - MUST resolve and validate all resolved IPs
            # This prevents DNS rebinding and hostnames like localtest.me (resolves to 127.0.0.1)
            try:
                # Resolve the hostname to all IP addresses
                addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                if not addr_info:
                    return False, f"Could not resolve hostname: {hostname}"

                # Check ALL resolved IPs - if any is private/internal, block
                for info in addr_info:
                    ip_str = info[4][0]
                    is_safe, error = _check_ip_is_safe(ip_str)
                    if not is_safe:
                        return False, f"Hostname '{hostname}' resolves to blocked address: {error}"

                return True, ""
            except socket.gaierror as e:
                return False, f"Could not resolve hostname '{hostname}': {e}"
            except Exception as e:
                return False, f"DNS resolution error for '{hostname}': {e}"

    except Exception as e:
        return False, f"Invalid URL: {e}"
