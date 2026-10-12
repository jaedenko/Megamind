# Connector interface (duck-typed): .name, .configured() -> bool, .fetch() -> iterable of
# {ext_id, title, description, date, url, media[], tags[]}. To add one: new file + one line here.
from .github import GitHub
from .notion import Notion
from .website import Website

CONNECTORS = [GitHub(), Notion(), Website()]
