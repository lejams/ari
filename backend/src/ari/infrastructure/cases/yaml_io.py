"""Unambiguous YAML/JSON, shared by legacy and v2 loaders."""

import json
from typing import Any

import yaml

from ari.domain.clinical import ClinicalBundle


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result or key == "<<":
            raise ValueError(f"Clé YAML dupliquée ou ambiguë: {key!r}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
# Keep ISO dates as strings for the JSON/schema boundary.
UniqueKeyLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in values if tag != "tag:yaml.org,2002:timestamp"]
    for key, values in UniqueKeyLoader.yaml_implicit_resolvers.items()
}


def read_yaml(text: str) -> Any:
    if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(text)):
        raise ValueError("Les alias YAML ne sont pas acceptés")
    return yaml.load(text, Loader=UniqueKeyLoader)


def parse_bundle(text: str) -> ClinicalBundle:
    return ClinicalBundle.model_validate_json(json.dumps(read_yaml(text), allow_nan=False))
