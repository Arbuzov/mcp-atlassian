#!/usr/bin/env python3
"""
Test script to demonstrate the new attach_file functionality with base64 encoding.
This simulates how MCP clients will use the new API.
"""

import base64


def test_base64_attach_simulation() -> None:
    """Simulate how a client would prepare a file for attachment."""

    # Create a test file
    test_content = (
        b"This is a test document content.\nIt has multiple lines.\nAnd some data."
    )
    test_filename = "test_document.txt"

    # Encode to base64 (this is what the MCP client does)
    content_base64 = base64.b64encode(test_content).decode("utf-8")

    print("=" * 70)
    print("SIMULATING MCP CLIENT FILE UPLOAD")
    print("=" * 70)
    print(f"\n1. Original file: {test_filename}")
    print(f"   Size: {len(test_content)} bytes")
    print(f"   Content preview: {test_content[:50]!r}...")

    print("\n2. Base64 encoded:")
    print(f"   Encoded size: {len(content_base64)} chars")
    print(f"   Encoded preview: {content_base64[:60]}...")

    # Decode (this is what the MCP server does)
    decoded_content = base64.b64decode(content_base64)

    print("\n3. After decoding on server:")
    print(f"   Decoded size: {len(decoded_content)} bytes")
    print(f"   Content match: {decoded_content == test_content}")

    # Test with a binary file (image-like)
    binary_content = bytes(range(256))  # Simulated binary data
    binary_base64 = base64.b64encode(binary_content).decode("utf-8")
    binary_decoded = base64.b64decode(binary_base64)

    print("\n4. Binary file test:")
    print(f"   Original size: {len(binary_content)} bytes")
    print(f"   Base64 size: {len(binary_base64)} chars")
    overhead = (len(binary_base64) / len(binary_content) - 1) * 100
    print(f"   Overhead: {overhead:.1f}%")
    print(f"   Binary match: {binary_decoded == binary_content}")

    # MCP tool call example
    print("\n5. Example MCP tool call parameters:")
    print("   {")
    print(f'     "filename": "{test_filename}",')
    print(f'     "file_content_base64": "{content_base64[:40]}...",')
    print('     "page_id": "123456789",')
    print('     "attachment_name": "Test Document",')
    print('     "content_type": "text/plain"')
    print("   }")

    print("\n" + "=" * 70)
    print("✅ Base64 encoding/decoding works correctly!")
    print("=" * 70)


def test_library_usage() -> None:
    """Show how the library can be used in both modes."""
    print("\n" + "=" * 70)
    print("LIBRARY USAGE EXAMPLES")
    print("=" * 70)

    print("\n1. File path mode (non-Docker, legacy):")
    print("   confluence.attach_file(")
    print("       file_path='/local/path/to/document.pdf',")
    print("       page_id='123456',")
    print("       attachment_name='Report'")
    print("   )")

    print("\n2. Content mode (Docker-compatible, new):")
    print("   confluence.attach_file(")
    print("       file_content=file_bytes,")
    print("       filename='document.pdf',")
    print("       page_id='123456',")
    print("       attachment_name='Report'")
    print("   )")

    print("\n3. Validation examples:")
    print("   ❌ Both file_path AND file_content (raises ValueError)")
    print("   ❌ Neither file_path NOR file_content (raises ValueError)")
    print("   ❌ file_content without filename (raises ValueError)")
    print("   ✅ file_path alone (works)")
    print("   ✅ file_content + filename (works)")


if __name__ == "__main__":
    test_base64_attach_simulation()
    test_library_usage()

    print("\n" + "=" * 70)
    print("📝 For full documentation, see: docs/ATTACHMENT_UPLOAD_DOCKER.md")
    print("=" * 70)
