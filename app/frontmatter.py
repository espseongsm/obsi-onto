"""Safe YAML with bounded construction; aliases are not accepted in indexed metadata."""

import yaml


class BoundedLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("YAML aliases in frontmatter are not supported.")
        self.nodes += 1
        self.depth += 1
        try:
            if self.depth > 20 or self.nodes > 4000:
                raise ValueError("The frontmatter structure is too complex.")
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1


def load_metadata(raw):
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise ValueError("Frontmatter must be no larger than 64 KiB.")
    try:
        value = yaml.load(raw, Loader=BoundedLoader) or {}
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("Check the frontmatter YAML syntax.") from exc
    if not isinstance(value, dict):
        raise ValueError("Frontmatter must be a mapping of properties.")
    return value
