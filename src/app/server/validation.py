from __future__ import annotations
from typing import Any, Dict, Tuple, List
from jsonschema import Draft202012Validator, ValidationError

def _fallback_schema() -> Dict[str, Any]:
    # used if plugin manifest has no proper schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["source", "language"],
        "properties": {
            "source": {"type": "string", "enum": ["prompt", "docx"]},
            "topic": {"type": "string"},
            "docx_url": {"type": "string", "format": "uri"},
            "language": {"type": "string", "enum": ["ar", "en"]},
            "slides_count": {"type": "integer", "minimum": 3, "maximum": 30},
            "theme": {"type": "string"},
            "images_query_pack": {
                "type": "array",
                "items": {"type": "string"}
            },
            "project_id": {"type": "string"}
        },
        "allOf": [
            {
                "if": {"properties": {"source": {"const": "prompt"}}},
                "then": {"required": ["topic"]}
            },
            {
                "if": {"properties": {"source": {"const": "docx"}}},
                "then": {"required": ["docx_url"]}
            }
        ]
    }

def extract_schema(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pull JSON-Schema from manifest.inputs. Accepts:
    - full JSON-Schema dict (with $schema/type)
    - minimal dict (type/required/properties)
    Falls back to a reasonable default if absent.
    """
    schema = manifest.get("inputs")
    if isinstance(schema, dict) and (schema.get("$schema") or schema.get("type") or schema.get("properties")):
        return schema
    return _fallback_schema()

def validate_inputs_against_schema(schema: Dict[str, Any], data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    validator = Draft202012Validator(schema)
    errors: List[ValidationError] = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if not errors:
        return True, []
    def to_err(e: ValidationError) -> Dict[str, Any]:
        path = "/".join([str(x) for x in e.absolute_path]) or "(root)"
        return {
            "path": path,
            "message": e.message,
            "validator": e.validator,
            "validator_value": e.validator_value,
            "schema_path": "/".join([str(p) for p in e.absolute_schema_path]),
        }
    return False, [to_err(e) for e in errors]
