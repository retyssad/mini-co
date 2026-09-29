"""mini-co - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.7.0"

from mini_co.agent import Agent
from mini_co.config import Config
from mini_co.llm import LLM
from mini_co.tools import ALL_TOOLS

__all__ = ["ALL_TOOLS", "LLM", "Agent", "Config", "__version__"]
