"""Run project tests without real services, secrets, or existing output writes.

Usage: uv run --offline --no-sync python tests/run_offline.py [test_module ...]
With no module arguments, discover the complete suite.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
for key in list(os.environ):
    if any(term in key.upper() for term in (
        "API_KEY", "LANGFUSE", "LLM_PROVIDER", "OPENAI", "GROQ", "GEMINI", "OLLAMA",
        "UNPAYWALL_EMAIL", "SEMANTIC_SCHOLAR", "OPENALEX",
    )):
        del os.environ[key]
os.environ.update(LANGFUSE_TRACING_ENABLED="false", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")


def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "subprocess.Popen", "os.system"}:
        raise RuntimeError("Offline tests blocked external operation: " + event)
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path == ROOT / ".env":
            raise RuntimeError("Offline tests blocked workspace .env")
        mode = args[1] or ""
        flags = args[2] if len(args) > 2 else 0
        writing = any(c in mode for c in "wax+") or bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        )
        if writing and (path.is_relative_to(ROOT / "data") or path == ROOT / "Summer_Project.pdf"):
            raise RuntimeError("Offline tests blocked existing project output writes")


class OfflineResult(unittest.TextTestResult):
    def startTest(self, test):
        self.previous_key = os.environ.get("GEMINI_API_KEY")
        if "test_pairwise_eval" in type(test).__module__:
            # These CLI tests mock build_report but instantiate an unused SDK client.
            os.environ["GEMINI_API_KEY"] = "AIza000"
        super().startTest(test)

    def stopTest(self, test):
        if self.previous_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self.previous_key
        super().stopTest(test)


def main():
    sys.addaudithook(audit)
    previous_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix="literature-review-offline-") as tmp:
            os.chdir(tmp)
            try:
                loader = unittest.TestLoader()
                suite = (loader.loadTestsFromNames(sys.argv[1:]) if sys.argv[1:]
                         else loader.discover(str(ROOT / "tests")))
                if suite.countTestCases() == 0:
                    raise RuntimeError("No tests discovered")
                result = unittest.TextTestRunner(verbosity=2, resultclass=OfflineResult).run(suite)
                return 0 if result.wasSuccessful() else 1
            finally:
                # Windows cannot remove a directory while it is the process cwd.
                os.chdir(previous_cwd)
    finally:
        os.chdir(previous_cwd)


if __name__ == "__main__":
    sys.exit(main())
