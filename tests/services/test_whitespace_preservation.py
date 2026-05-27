"""
Tests for whitespace preservation in code artifact pipeline.

Covers the root cause identified in answer.log: streaming chunk assembly
stripping inter-token whitespace, causing '#ifndef SINGLETON_H' to become
'#ifndefSINGLETON_H'.
"""

from __future__ import annotations

import pytest


class TestExtractChunkStreamParts:
    """Verify extract_chunk_stream_parts preserves intra-token whitespace."""

    def test_preserves_leading_space_in_text_chunk(self):
        """Streaming token ' SINGLETON_H' must retain its leading space."""
        from app.services.llm_gateway import extract_chunk_stream_parts

        thinking, text = extract_chunk_stream_parts(" SINGLETON_H")
        assert text == " SINGLETON_H"
        assert thinking == ""

    def test_preserves_trailing_space_in_text_chunk(self):
        from app.services.llm_gateway import extract_chunk_stream_parts

        thinking, text = extract_chunk_stream_parts("#ifndef ")
        assert text == "#ifndef "
        assert thinking == ""

    def test_preserves_indentation_chunk(self):
        """A chunk that is pure indentation (4 spaces + code) must keep spaces."""
        from app.services.llm_gateway import extract_chunk_stream_parts

        thinking, text = extract_chunk_stream_parts("    int x = 0;")
        assert text == "    int x = 0;"
        assert thinking == ""

    def test_empty_string_returns_empty(self):
        from app.services.llm_gateway import extract_chunk_stream_parts

        thinking, text = extract_chunk_stream_parts("")
        assert text == ""
        assert thinking == ""

    def test_whitespace_only_passes_through(self):
        """Pure whitespace chunks (e.g. newline tokens) must pass through so the
        caller can assemble them correctly into the full response."""
        from app.services.llm_gateway import extract_chunk_stream_parts

        thinking, text = extract_chunk_stream_parts("\n")
        assert text == "\n"
        assert thinking == ""

    def test_none_returns_empty(self):
        from app.services.llm_gateway import extract_chunk_stream_parts

        thinking, text = extract_chunk_stream_parts(None)
        assert text == ""
        assert thinking == ""

    def test_thinking_block_detection_not_stripped(self):
        """A JSON thinking block should be returned as thinking, not stripped."""
        from app.services.llm_gateway import extract_chunk_stream_parts

        content = '{"thinking": "let me think about this"}'
        thinking, text = extract_chunk_stream_parts(content)
        assert thinking == content
        assert text == ""

    def test_streaming_chunk_assembly_preserves_code(self):
        """Simulate streaming assembly of '#ifndef SINGLETON_H'."""
        from app.services.llm_gateway import extract_chunk_stream_parts

        # Each item is one streaming token
        chunks = ["#ifndef", " SINGLETON_H", "\n", "#define", " SINGLETON_H"]
        assembled = ""
        for chunk in chunks:
            _thinking, text = extract_chunk_stream_parts(chunk)
            assembled += text

        assert assembled == "#ifndef SINGLETON_H\n#define SINGLETON_H"

    def test_class_declaration_streaming(self):
        """Simulate 'class Singleton {' streamed as separate tokens."""
        from app.services.llm_gateway import extract_chunk_stream_parts

        chunks = ["class", " Singleton", " {"]
        assembled = ""
        for chunk in chunks:
            _thinking, text = extract_chunk_stream_parts(chunk)
            assembled += text

        assert assembled == "class Singleton {"


class TestSanitizeCollapsedWhitespace:
    """Tests for the defensive whitespace-collapse sanitizer."""

    def test_fixes_ifndef_collapse(self):
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "#ifndefSINGLETON_H\n#defineSINGLETON_H"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "#ifndef SINGLETON_H" in fixed
        assert "#define SINGLETON_H" in fixed

    def test_fixes_class_collapse(self):
        """classSingleton → class Singleton (uppercase follow-on)."""
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "classSingleton{"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "class Singleton" in fixed

    def test_fixes_include_collapse(self):
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "#include<iostream>"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "#include <iostream>" in fixed

    def test_preserves_correct_code(self):
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "#ifndef SINGLETON_H\n#define SINGLETON_H\nclass Singleton {\n};"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert fixed == code

    def test_fixes_struct_collapse(self):
        """structNode → struct Node (uppercase follow-on)."""
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "structNode{"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "struct Node" in fixed

    def test_does_not_break_lowercase_identifier(self):
        """returnx is an ambiguous lower-case identifier; sanitizer should NOT
        insert a space when follow-on starts with lowercase (no case signal)."""
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "int returnx = 1;"  # 'returnx' is a valid variable name
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "returnx" in fixed

    def test_fixes_template_collapse(self):
        """templateMyClass → template MyClass."""
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "templateMyClass"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "template MyClass" in fixed

    def test_handles_endif_with_comment(self):
        from app.services.code_artifact_pipeline import _sanitize_collapsed_whitespace

        code = "#endif//SINGLETON_H"
        fixed = _sanitize_collapsed_whitespace(code, "cpp")
        assert "#endif //" in fixed or "#endif//" in fixed


class TestEnsureCodeArtifactsQualitySanitization:
    """Integration test: ensure_code_artifacts_quality runs sanitization pass."""

    def test_sanitizes_before_verify(self, monkeypatch):
        """Collapsed code should be sanitized before any verify attempt."""
        from app.services import code_artifact_pipeline as cap

        # Disable verify so we only test sanitization
        monkeypatch.setattr(
            cap, "load_code_artifact_config",
            lambda: cap.CodeArtifactConfig(enabled=True, repair_enabled=False),
        )
        monkeypatch.setattr(cap, "should_process_code_artifacts", lambda state=None: True)

        # Mock _run_compile_verify_loop to return structured as-is
        def mock_verify_loop(structured, **kwargs):
            return structured

        monkeypatch.setattr(cap, "_run_compile_verify_loop", mock_verify_loop)

        reasoning_result = {
            "summary": "test",
            "structured": {
                "artifacts": [
                    {
                        "kind": "code",
                        "language": "cpp",
                        # Uppercase follow-on identifiers → should be fixed
                        "content": "#ifndefFOO_H\nclassBar{\n};",
                    }
                ]
            },
        }

        result = cap.ensure_code_artifacts_quality(reasoning_result)
        artifacts = result["structured"]["artifacts"]
        code = artifacts[0]["content"]
        assert "#ifndef FOO_H" in code
        assert "class Bar" in code
