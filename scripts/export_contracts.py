"""Export BSON-compatible, inlined Mongo schemas from the shared Pydantic contracts."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import ROOT
from shared.models import Alert, Disruption, RefineRequest, Signal, Watch


def mongo_schema(schema):
    definitions = schema.get("$defs", {})

    def translate(node):
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return translate(definitions[node["$ref"].split("/")[-1]])
        out = {}
        for key, value in node.items():
            if key in {"$defs", "title", "default", "format"}:
                continue
            if key == "type":
                out["bsonType"] = {
                    "integer": ["int", "long"],
                    "number": ["double", "int", "long"],
                    "boolean": "bool",
                }.get(value, value)
            elif key in {"properties", "additionalProperties"} and isinstance(value, dict):
                out[key] = (
                    {k: translate(v) for k, v in value.items()}
                    if key == "properties"
                    else translate(value)
                )
            elif isinstance(value, list) and key in {"anyOf", "allOf", "oneOf"}:
                out[key] = [translate(item) for item in value]
            elif key == "items":
                out[key] = translate(value)
            else:
                out[key] = value
        if node.get("format") == "date-time":
            out["bsonType"] = "date"
        return out

    return {"$jsonSchema": translate(schema)}


if __name__ == "__main__":
    folder = ROOT / "shared" / "schemas"
    folder.mkdir(parents=True, exist_ok=True)
    for name, model in {
        "watches": Watch,
        "signals": Signal,
        "disruptions": Disruption,
        "alerts": Alert,
        "refine_requests": RefineRequest,
    }.items():
        (folder / (name + ".json")).write_text(
            json.dumps(mongo_schema(model.model_json_schema()), indent=2) + "\n"
        )
    print("OK exported Track 1 document contracts")
