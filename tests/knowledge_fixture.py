"""Synthetic source pages for offline tests; never injected by the live launcher."""
from app.knowledge_sources import PageText


class SyntheticKnowledgeAdapter:
    def __init__(self):
        self.calls = []
        self.version = 1
        self.failures = set()

    def fetch(self, source_id):
        self.calls.append(source_id)
        if source_id in self.failures:
            raise TimeoutError("Synthetic inaccessible source")
        parser = PageText()
        parser.feed("<html><head><title>SYNTHETIC source review</title><meta property='article:published_time' content='2026-09-01'></head><body><main><h1>SYNTHETIC TEST DATA</h1><p>These are invented fixture statements for source-check testing only. They are not market data, legal guidance, a public article, or evidence about an actual transaction.</p><p>Source " + source_id + " synthetic fixture version " + str(self.version) + " has an additional reviewed change for automated workflow testing.</p></main></body></html>")
        return parser.snapshot()
