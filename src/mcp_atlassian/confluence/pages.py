"""Module for Confluence page operations."""

import logging
import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import HTTPError

from ..exceptions import MCPAtlassianAuthenticationError
from ..models.confluence import ConfluenceAttachment, ConfluencePage
from .client import ConfluenceClient
from .v2_adapter import ConfluenceV2Adapter

logger = logging.getLogger("mcp-atlassian")


@contextmanager
def temporary_file_from_bytes(
    content: bytes, suffix: str = ""
) -> Generator[str, None, None]:
    """Context manager for creating and cleaning up temporary files.

    Args:
        content: Bytes content to write to the temporary file.
        suffix: Optional suffix for the temporary file.

    Yields:
        str: Path to the temporary file.

    Raises:
        OSError: If file creation or cleanup fails.
    """
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        temp_file.write(content)
        temp_file.flush()
        temp_file.close()
        yield temp_file.name
    except OSError as exc:
        # Clean up on creation error
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
        logger.error("Error creating temporary file: %s", exc)
        raise
    finally:
        # Always clean up the temporary file
        if os.path.exists(temp_file.name):
            try:
                os.unlink(temp_file.name)
            except OSError as cleanup_exc:
                logger.warning(
                    "Failed to clean up temporary file '%s': %s",
                    temp_file.name,
                    cleanup_exc,
                )


class PagesMixin(ConfluenceClient):
    """Mixin for Confluence page operations."""

    @property
    def _v2_adapter(self) -> ConfluenceV2Adapter | None:
        """Get v2 API adapter for OAuth authentication.

        Returns:
            ConfluenceV2Adapter instance if OAuth is configured, None otherwise
        """
        if self.config.auth_type == "oauth" and self.config.is_cloud:
            return ConfluenceV2Adapter(
                session=self.confluence._session, base_url=self.confluence.url
            )
        return None

    def get_page_content(
        self, page_id: str, *, convert_to_markdown: bool = True
    ) -> ConfluencePage:
        """
        Get content of a specific page.

        Args:
            page_id: The ID of the page to retrieve
            convert_to_markdown: When True, returns content in markdown format,
                               otherwise returns raw HTML (keyword-only)

        Returns:
            ConfluencePage model containing the page content and metadata

        Raises:
            MCPAtlassianAuthenticationError: If authentication fails with the Confluence API (401/403)
            Exception: If there is an error retrieving the page
        """
        try:
            # Use v2 API for OAuth authentication, v1 API for token/basic auth
            v2_adapter = self._v2_adapter
            if v2_adapter:
                logger.debug(
                    f"Using v2 API for OAuth authentication to get page '{page_id}'"
                )
                page = v2_adapter.get_page(
                    page_id=page_id,
                    expand="body.storage,version,space,children.attachment",
                )
            else:
                logger.debug(
                    f"Using v1 API for token/basic authentication to get page '{page_id}'"
                )
                page = self.confluence.get_page_by_id(
                    page_id=page_id,
                    expand="body.storage,version,space,children.attachment",
                )

            space_key = page.get("space", {}).get("key", "")
            content = page["body"]["storage"]["value"]
            processed_html, processed_markdown = self.preprocessor.process_html_content(
                content, space_key=space_key, confluence_client=self.confluence
            )

            # Use the appropriate content format based on the convert_to_markdown flag
            page_content = processed_markdown if convert_to_markdown else processed_html

            # Create and return the ConfluencePage model
            return ConfluencePage.from_api_response(
                page,
                base_url=self.config.url,
                include_body=True,
                # Override content with our processed version
                content_override=page_content,
                content_format="storage" if not convert_to_markdown else "markdown",
                is_cloud=self.config.is_cloud,
            )
        except HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code in [
                401,
                403,
            ]:
                error_msg = (
                    f"Authentication failed for Confluence API ({http_err.response.status_code}). "
                    "Token may be expired or invalid. Please verify credentials."
                )
                logger.error(error_msg)
                raise MCPAtlassianAuthenticationError(error_msg) from http_err
            else:
                logger.error(f"HTTP error during API call: {http_err}", exc_info=False)
                raise http_err
        except Exception as e:
            logger.error(
                f"Error retrieving page content for page ID {page_id}: {str(e)}"
            )
            raise Exception(f"Error retrieving page content: {str(e)}") from e

    def get_page_ancestors(self, page_id: str) -> list[ConfluencePage]:
        """
        Get ancestors (parent pages) of a specific page.

        Args:
            page_id: The ID of the page to get ancestors for

        Returns:
            List of ConfluencePage models representing the ancestors in hierarchical order
                (immediate parent first, root ancestor last)

        Raises:
            MCPAtlassianAuthenticationError: If authentication fails with the Confluence API (401/403)
        """
        try:
            # Use the Atlassian Python API to get ancestors
            ancestors = self.confluence.get_page_ancestors(page_id)

            # Process each ancestor
            ancestor_models = []
            for ancestor in ancestors:
                # Create the page model without fetching content
                page_model = ConfluencePage.from_api_response(
                    ancestor,
                    base_url=self.config.url,
                    include_body=False,
                )
                ancestor_models.append(page_model)

            return ancestor_models
        except HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code in [
                401,
                403,
            ]:
                error_msg = (
                    f"Authentication failed for Confluence API ({http_err.response.status_code}). "
                    "Token may be expired or invalid. Please verify credentials."
                )
                logger.error(error_msg)
                raise MCPAtlassianAuthenticationError(error_msg) from http_err
            else:
                logger.error(f"HTTP error during API call: {http_err}", exc_info=False)
                raise http_err
        except Exception as e:
            logger.error(f"Error fetching ancestors for page {page_id}: {str(e)}")
            logger.debug("Full exception details:", exc_info=True)
            return []

    def get_page_by_title(
        self, space_key: str, title: str, *, convert_to_markdown: bool = True
    ) -> ConfluencePage | None:
        """
        Get a specific page by its title from a Confluence space.

        Args:
            space_key: The key of the space containing the page
            title: The title of the page to retrieve
            convert_to_markdown: When True, returns content in markdown format,
                               otherwise returns raw HTML (keyword-only)

        Returns:
            ConfluencePage model containing the page content and metadata, or None if not found
        """
        try:
            # Directly try to find the page by title
            page = self.confluence.get_page_by_title(
                space=space_key, title=title, expand="body.storage,version"
            )

            if not page:
                logger.warning(
                    f"Page '{title}' not found in space '{space_key}'. "
                    f"The space may be invalid, the page may not exist, or permissions may be insufficient."
                )
                return None

            content = page["body"]["storage"]["value"]
            processed_html, processed_markdown = self.preprocessor.process_html_content(
                content, space_key=space_key, confluence_client=self.confluence
            )

            # Use the appropriate content format based on the convert_to_markdown flag
            page_content = processed_markdown if convert_to_markdown else processed_html

            # Create and return the ConfluencePage model
            return ConfluencePage.from_api_response(
                page,
                base_url=self.config.url,
                include_body=True,
                # Override content with our processed version
                content_override=page_content,
                content_format="storage" if not convert_to_markdown else "markdown",
                is_cloud=self.config.is_cloud,
            )

        except KeyError as e:
            logger.error(f"Missing key in page data: {str(e)}")
            return None
        except requests.RequestException as e:
            logger.error(f"Network error when fetching page: {str(e)}")
            return None
        except (ValueError, TypeError) as e:
            logger.error(f"Error processing page data: {str(e)}")
            return None
        except Exception as e:  # noqa: BLE001 - Intentional fallback with full logging
            logger.error(f"Unexpected error fetching page: {str(e)}")
            # Log the full traceback at debug level for troubleshooting
            logger.debug("Full exception details:", exc_info=True)
            return None

    def get_space_pages(
        self,
        space_key: str,
        start: int = 0,
        limit: int = 10,
        *,
        convert_to_markdown: bool = True,
    ) -> list[ConfluencePage]:
        """
        Get all pages from a specific space.

        Args:
            space_key: The key of the space to get pages from
            start: The starting index for pagination
            limit: Maximum number of pages to return
            convert_to_markdown: When True, returns content in markdown format,
                               otherwise returns raw HTML (keyword-only)

        Returns:
            List of ConfluencePage models containing page content and metadata
        """
        pages = self.confluence.get_all_pages_from_space(
            space=space_key, start=start, limit=limit, expand="body.storage"
        )

        page_models = []
        for page in pages:
            content = page["body"]["storage"]["value"]
            processed_html, processed_markdown = self.preprocessor.process_html_content(
                content, space_key=space_key, confluence_client=self.confluence
            )

            # Use the appropriate content format based on the convert_to_markdown flag
            page_content = processed_markdown if convert_to_markdown else processed_html

            # Ensure space information is included
            if "space" not in page:
                page["space"] = {
                    "key": space_key,
                    "name": space_key,  # Use space_key as name if not available
                }

            # Create the ConfluencePage model
            page_model = ConfluencePage.from_api_response(
                page,
                base_url=self.config.url,
                include_body=True,
                # Override content with our processed version
                content_override=page_content,
                content_format="storage" if not convert_to_markdown else "markdown",
                is_cloud=self.config.is_cloud,
            )

            page_models.append(page_model)

        return page_models

    def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        parent_id: str | None = None,
        *,
        is_markdown: bool = True,
        enable_heading_anchors: bool = False,
        content_representation: str | None = None,
    ) -> ConfluencePage:
        """
        Create a new page in a Confluence space.

        Args:
            space_key: The key of the space to create the page in
            title: The title of the new page
            body: The content of the page (markdown, wiki markup, or storage format)
            parent_id: Optional ID of a parent page
            is_markdown: Whether the body content is in markdown format (default: True, keyword-only)
            enable_heading_anchors: Whether to enable automatic heading anchor generation (default: False, keyword-only)
            content_representation: Content format when is_markdown=False ('wiki' or 'storage', keyword-only)

        Returns:
            ConfluencePage model containing the new page's data

        Raises:
            Exception: If there is an error creating the page
        """
        try:
            # Determine body and representation based on content type
            if is_markdown:
                # Convert markdown to Confluence storage format
                final_body = self.preprocessor.markdown_to_confluence_storage(
                    body, enable_heading_anchors=enable_heading_anchors
                )
                representation = "storage"
            else:
                # Use body as-is with specified representation
                final_body = body
                representation = content_representation or "storage"

            # Use v2 API for OAuth authentication, v1 API for token/basic auth
            v2_adapter = self._v2_adapter
            if v2_adapter:
                logger.debug(
                    f"Using v2 API for OAuth authentication to create page '{title}'"
                )
                result = v2_adapter.create_page(
                    space_key=space_key,
                    title=title,
                    body=final_body,
                    parent_id=parent_id,
                    representation=representation,
                )
            else:
                logger.debug(
                    f"Using v1 API for token/basic authentication to create page '{title}'"
                )
                result = self.confluence.create_page(
                    space=space_key,
                    title=title,
                    body=final_body,
                    parent_id=parent_id,
                    representation=representation,
                )

            # Get the new page content
            page_id = result.get("id")
            if not page_id:
                raise ValueError("Create page response did not contain an ID")

            return self.get_page_content(page_id)
        except Exception as e:
            logger.error(
                f"Error creating page '{title}' in space {space_key}: {str(e)}"
            )
            raise Exception(
                f"Failed to create page '{title}' in space {space_key}: {str(e)}"
            ) from e

    def update_page(
        self,
        page_id: str,
        title: str,
        body: str,
        *,
        is_minor_edit: bool = False,
        version_comment: str = "",
        is_markdown: bool = True,
        parent_id: str | None = None,
        enable_heading_anchors: bool = False,
        content_representation: str | None = None,
    ) -> ConfluencePage:
        """
        Update an existing page in Confluence.

        Args:
            page_id: The ID of the page to update
            title: The new title of the page
            body: The new content of the page (markdown, wiki markup, or storage format)
            is_minor_edit: Whether this is a minor edit (keyword-only)
            version_comment: Optional comment for this version (keyword-only)
            is_markdown: Whether the body content is in markdown format (default: True, keyword-only)
            parent_id: Optional new parent page ID (keyword-only)
            enable_heading_anchors: Whether to enable automatic heading anchor generation (default: False, keyword-only)
            content_representation: Content format when is_markdown=False ('wiki' or 'storage', keyword-only)

        Returns:
            ConfluencePage model containing the updated page's data

        Raises:
            Exception: If there is an error updating the page
        """
        try:
            # Determine body and representation based on content type
            if is_markdown:
                # Convert markdown to Confluence storage format
                final_body = self.preprocessor.markdown_to_confluence_storage(
                    body, enable_heading_anchors=enable_heading_anchors
                )
                representation = "storage"
            else:
                # Use body as-is with specified representation
                final_body = body
                representation = content_representation or "storage"

            logger.debug(f"Updating page {page_id} with title '{title}'")

            # Use v2 API for OAuth authentication, v1 API for token/basic auth
            v2_adapter = self._v2_adapter
            if v2_adapter:
                logger.debug(
                    f"Using v2 API for OAuth authentication to update page '{page_id}'"
                )
                response = v2_adapter.update_page(
                    page_id=page_id,
                    title=title,
                    body=final_body,
                    representation=representation,
                    version_comment=version_comment,
                )
            else:
                logger.debug(
                    f"Using v1 API for token/basic authentication to update page '{page_id}'"
                )
                update_kwargs = {
                    "page_id": page_id,
                    "title": title,
                    "body": final_body,
                    "type": "page",
                    "representation": representation,
                    "minor_edit": is_minor_edit,
                    "version_comment": version_comment,
                    "always_update": True,
                }
                if parent_id:
                    update_kwargs["parent_id"] = parent_id

                self.confluence.update_page(**update_kwargs)

            # After update, refresh the page data
            return self.get_page_content(page_id)
        except Exception as e:
            logger.error(f"Error updating page {page_id}: {str(e)}")
            raise Exception(f"Failed to update page {page_id}: {str(e)}") from e

    def get_page_children(
        self,
        page_id: str,
        start: int = 0,
        limit: int = 25,
        expand: str = "version",
        *,
        convert_to_markdown: bool = True,
    ) -> list[ConfluencePage]:
        """
        Get child pages of a specific Confluence page.

        Args:
            page_id: The ID of the parent page
            start: The starting index for pagination
            limit: Maximum number of child pages to return
            expand: Fields to expand in the response
            convert_to_markdown: When True, returns content in markdown format,
                               otherwise returns raw HTML (keyword-only)

        Returns:
            List of ConfluencePage models containing the child pages
        """
        try:
            # Use the Atlassian Python API's get_page_child_by_type method
            results = self.confluence.get_page_child_by_type(
                page_id=page_id, type="page", start=start, limit=limit, expand=expand
            )

            # Process results
            page_models = []

            # Handle both pagination modes
            if isinstance(results, dict) and "results" in results:
                child_pages = results.get("results", [])
            else:
                child_pages = results or []

            space_key = ""

            # Get space key from the first result if available
            if child_pages and "space" in child_pages[0]:
                space_key = child_pages[0].get("space", {}).get("key", "")

            # Process each child page
            for page in child_pages:
                # Only process content if we have "body" expanded
                content_override = None
                if "body" in page and convert_to_markdown:
                    content = page.get("body", {}).get("storage", {}).get("value", "")
                    if content:
                        _, processed_markdown = self.preprocessor.process_html_content(
                            content,
                            space_key=space_key,
                            confluence_client=self.confluence,
                        )
                        content_override = processed_markdown

                # Create the page model
                page_model = ConfluencePage.from_api_response(
                    page,
                    base_url=self.config.url,
                    include_body=True,
                    content_override=content_override,
                    content_format="markdown" if convert_to_markdown else "storage",
                )

                page_models.append(page_model)

            return page_models

        except Exception as e:
            logger.error(f"Error fetching child pages for page {page_id}: {str(e)}")
            logger.debug("Full exception details:", exc_info=True)
            return []

    def _validate_attachment_params(
        self,
        file_path: str | Path | None,
        file_content: bytes | None,
        filename: str | None,
        page_id: str | None,
        space_key: str | None,
        title: str | None,
    ) -> None:
        """Validate attachment upload parameters.

        Args:
            file_path: File path (if using path mode).
            file_content: File content bytes (if using content mode).
            filename: Filename (required for content mode).
            page_id: Page ID target.
            space_key: Space key (used with title).
            title: Page title (used with space_key).

        Raises:
            ValueError: If parameters are invalid or conflicting.
        """
        # Validate file input modes
        if file_path and file_content:
            error_msg = "Provide either file_path or file_content, not both"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if not file_path and not file_content:
            error_msg = "Must provide either file_path or file_content"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if file_content and not filename:
            error_msg = "filename is required when using file_content"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate target page parameters
        if not page_id and not (space_key and title):
            error_msg = (
                "attach_file requires either page_id or both space_key and title"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Warn about conflicting parameters
        if page_id and (space_key or title):
            logger.warning(
                "Both page_id and space_key/title provided. "
                "Using page_id and ignoring space_key/title. "
                f"page_id={page_id}, space_key={space_key}, title={title}"
            )

    def _prepare_file_for_upload(
        self,
        file_path: str | Path | None,
        file_content: bytes | None,
        filename: str | None,
        attachment_name: str | None,
    ) -> tuple[str, str]:
        """Prepare file for upload by validating path or creating temporary file.

        Args:
            file_path: File path (if using path mode).
            file_content: File content bytes (if using content mode).
            filename: Filename (required for content mode).
            attachment_name: Optional display name for attachment.

        Returns:
            Tuple of (file_to_upload_path, actual_filename).

        Raises:
            FileNotFoundError: If file_path doesn't exist.
        """
        if file_path:
            path = Path(file_path).expanduser()
            if not path.is_file():
                error_msg = (
                    f"Attachment file not found. "
                    f"Original input: '{file_path}', expanded path: '{path}'"
                )
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)
            return str(path), attachment_name or path.name
        else:
            # Content mode - will use context manager in attach_file
            return "", attachment_name or filename  # type: ignore[return-value]

    def _upload_attachment(
        self,
        file_to_upload: str,
        actual_filename: str,
        page_id: str | None,
        space_key: str | None,
        title: str | None,
        content_type: str | None,
        comment: str | None,
    ) -> dict[str, Any]:
        """Upload attachment to Confluence API.

        Args:
            file_to_upload: Path to file to upload.
            actual_filename: Display name for attachment.
            page_id: Page ID target.
            space_key: Space key (used with title).
            title: Page title (used with space_key).
            content_type: MIME type.
            comment: Attachment comment.

        Returns:
            API response dictionary.

        Raises:
            MCPAtlassianAuthenticationError: If authentication fails.
            HTTPError: For other HTTP errors.
        """
        if page_id:
            logger.debug(
                "Uploading attachment '%s' to page ID %s", actual_filename, page_id
            )
        else:
            logger.debug(
                "Uploading attachment '%s' to page '%s' in space '%s'",
                actual_filename,
                title,
                space_key,
            )

        try:
            response = self.confluence.attach_file(
                filename=file_to_upload,
                name=actual_filename,
                content_type=content_type,
                page_id=page_id,
                title=title,
                space=space_key,
                comment=comment,
            )
            return response
        except HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code in [
                401,
                403,
            ]:
                error_msg = (
                    "Authentication failed when uploading attachment to Confluence "
                    f"({http_err.response.status_code})."
                )
                logger.error(error_msg)
                raise MCPAtlassianAuthenticationError(error_msg) from http_err
            logger.error(
                "HTTP error while uploading attachment '%s': %s",
                actual_filename,
                http_err,
            )
            raise

    def attach_file(
        self,
        file_path: str | Path | None = None,
        file_content: bytes | None = None,
        filename: str | None = None,
        *,
        page_id: str | None = None,
        space_key: str | None = None,
        title: str | None = None,
        attachment_name: str | None = None,
        content_type: str | None = None,
        comment: str | None = None,
    ) -> ConfluenceAttachment:
        """Upload a file as an attachment to a Confluence page.

        Supports two modes:
        1. File path mode (legacy): Provide ``file_path``
        2. Content mode (for Docker/remote): Provide ``file_content`` and ``filename``

        Args:
            file_path: Local path to the file (for local/non-Docker use).
            file_content: Raw bytes content of the file (for Docker/remote use).
            filename: Name for the file when using ``file_content`` mode.
            page_id: ID of the page to attach the file to. When provided, overrides
                ``space_key`` and ``title``.
            space_key: Key of the space that contains the page (used with ``title``).
            title: Title of the target page (used with ``space_key``).
            attachment_name: Optional name for the attachment. Defaults to the
                filename when not provided.
            content_type: Optional MIME type for the attachment payload.
            comment: Optional comment stored with the attachment metadata.

        Returns:
            ConfluenceAttachment representing the uploaded file.

        Raises:
            FileNotFoundError: If ``file_path`` does not exist or is not a file.
            ValueError: If neither ``page_id`` nor a ``space_key``/``title`` pair is
                provided, if both or neither of ``file_path``/``file_content`` are
                provided, or if the API response cannot be parsed.
            MCPAtlassianAuthenticationError: If authentication fails.
            HTTPError: If the Confluence REST API returns an HTTP error other than
                authentication failures.
        """
        # Validate all parameters
        self._validate_attachment_params(
            file_path, file_content, filename, page_id, space_key, title
        )

        # Prepare file and get filename
        file_to_upload, actual_filename = self._prepare_file_for_upload(
            file_path, file_content, filename, attachment_name
        )

        # Upload with context manager for content mode
        if file_content:
            with temporary_file_from_bytes(file_content, suffix=f"_{filename}") as tmp:
                response = self._upload_attachment(
                    tmp,
                    actual_filename,
                    page_id,
                    space_key,
                    title,
                    content_type,
                    comment,
                )
        else:
            response = self._upload_attachment(
                file_to_upload,
                actual_filename,
                page_id,
                space_key,
                title,
                content_type,
                comment,
            )

        attachment_payload = self._extract_attachment_payload(response)
        return ConfluenceAttachment.from_api_response(attachment_payload)

    def _extract_attachment_payload(self, response: Any) -> dict[str, Any]:
        """Extract attachment payload from Confluence API response.

        Args:
            response: The raw API response from Confluence.

        Returns:
            Dictionary containing the attachment data.

        Raises:
            ValueError: If the response cannot be parsed as a valid attachment.
        """
        attachment_payload: dict[str, Any] | None = None

        if isinstance(response, dict):
            # Check for paginated results format
            if isinstance(response.get("results"), list) and response["results"]:
                first_result = response["results"][0]
                if isinstance(first_result, dict):
                    attachment_payload = first_result
            # Check for single attachment format
            elif response.get("type") == "attachment":
                attachment_payload = response

        if not attachment_payload:
            logger.debug(
                "Raw attachment response that could not be parsed: %s", response
            )
            error_msg = "Unable to parse attachment response from Confluence API"
            logger.error(error_msg)
            raise ValueError(error_msg)

        return attachment_payload

    def move_page(
        self,
        page_id: str,
        *,
        space_key: str | None = None,
        parent_id: str | None = None,
        position: str = "append",
    ) -> bool:
        """Move a Confluence page to a different parent and/or space.

        Args:
            page_id: The ID of the page to move
            space_key: Destination space key. If omitted, uses the page's current space
            parent_id: Destination parent page ID. If None, moves page to space root
            position: Position relative to the parent page (default: "append")

        Returns:
            True if the page was moved successfully, False otherwise

        Raises:
            ValueError: If space_key is not provided and cannot be resolved
            Exception: If there is an error moving the page
        """
        try:
            if space_key is None:
                page = self.confluence.get_page_by_id(page_id, expand="space")
                space_key = page.get("space", {}).get("key")
            if not space_key:
                raise ValueError("space_key must be provided or resolvable from page")

            v2_adapter = self._v2_adapter
            move_position = position if parent_id else "topLevel"

            if v2_adapter:
                return v2_adapter.move_page(
                    page_id=page_id,
                    space_key=space_key,
                    parent_id=parent_id,
                    position=move_position,
                )

            self.confluence.move_page(
                space_key=space_key,
                page_id=page_id,
                target_id=parent_id,
                position=move_position,
            )
            return True
        except ValueError as e:
            logger.error(f"Error moving page {page_id}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error moving page {page_id}: {str(e)}")
            raise Exception(f"Failed to move page {page_id}: {str(e)}") from e

    def delete_page(self, page_id: str) -> bool:
        """
        Delete a Confluence page by its ID.

        Args:
            page_id: The ID of the page to delete

        Returns:
            Boolean indicating success (True) or failure (False)

        Raises:
            Exception: If there is an error deleting the page
        """
        try:
            logger.debug(f"Deleting page {page_id}")

            # Use v2 API for OAuth authentication, v1 API for token/basic auth
            v2_adapter = self._v2_adapter
            if v2_adapter:
                logger.debug(
                    f"Using v2 API for OAuth authentication to delete page '{page_id}'"
                )
                return v2_adapter.delete_page(page_id=page_id)
            else:
                logger.debug(
                    f"Using v1 API for token/basic authentication to delete page '{page_id}'"
                )
                response = self.confluence.remove_page(page_id=page_id)

                # The Atlassian library's remove_page returns the raw response from
                # the REST API call. For a successful deletion, we should get a
                # response object, but it might be empty (HTTP 204 No Content).
                # For REST DELETE operations, a success typically returns 204 or 200

                # Check if we got a response object
                if isinstance(response, requests.Response):
                    # Check if status code indicates success (2xx)
                    success = 200 <= response.status_code < 300
                    logger.debug(
                        f"Delete page {page_id} returned status code {response.status_code}"
                    )
                    return success
                # If it's not a response object but truthy (like True), consider it a success
                elif response:
                    return True
                # Default to true since no exception was raised
                # This is safer than returning false when we don't know what happened
                return True

        except Exception as e:
            logger.error(f"Error deleting page {page_id}: {str(e)}")
            raise Exception(f"Failed to delete page {page_id}: {str(e)}") from e
