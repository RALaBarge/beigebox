"""
Operator Screen — interact with the BeigeBox Operator agent in the TUI.
Ask questions about conversations, system state, and data stores.
"""
from __future__ import annotations
import asyncio
from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Input, Button, RichLog
from textual.reactive import reactive
from beigebox.tui.screens.base import BeigeBoxPane
from beigebox.agents.operator import Operator
from beigebox.config import get_config
from beigebox.storage.vector_store import VectorStore


class OperatorScreen(BeigeBoxPane):
    """Interactive Operator agent screen."""
    
    DEFAULT_CSS = """
    OperatorScreen {
        layout: vertical;
        height: 100%;
    }
    #operator-log {
        height: 1fr;
        border: solid $accent;
        background: $panel;
    }
    #operator-input {
        height: auto;
        border: solid $accent;
    }
    #operator-buttons {
        height: auto;
        layout: horizontal;
        border: solid $accent;
    }
    Button {
        margin: 0 1;
    }
    """
    
    operator_ready: reactive[bool] = reactive(False)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.operator: Operator | None = None
        self.vector_store: VectorStore | None = None
        self._init_task: asyncio.Task | None = None
    
    def compose(self) -> ComposeResult:
        yield RichLog(id="operator-log", markup=True)
        with Horizontal(id="operator-buttons"):
            yield Input(
                placeholder="Ask me something... (web search, conversation history, shell commands, stats)",
                id="operator-input"
            )
            yield Button("Send", id="operator-send", variant="primary")
            yield Button("Clear", id="operator-clear")
    
    def on_mount(self) -> None:
        """Initialize the operator in the background."""
        self._init_task = asyncio.create_task(self._init_operator())
    
    async def _init_operator(self) -> None:
        """Load the operator asynchronously."""
        try:
            cfg = get_config()
            # Load vector store for semantic search
            try:
                self.vector_store = VectorStore(
                    chroma_path=cfg["storage"]["chroma_path"],
                    embedding_model=cfg["embedding"]["model"],
                    embedding_url=cfg["embedding"]["backend_url"],
                )
            except Exception as e:
                self.log(f"[yellow]⚠ Vector store unavailable: {e}[/yellow]")
                self.vector_store = None
            
            # Load operator
            self.operator = Operator(vector_store=self.vector_store)
            self.operator_ready = True
            self.log("[green]✓ Operator online[/green]")
            self.log("[cyan]Available tools:[/cyan]")
            for tool in self.operator.tools:
                self.log(f"  ⚡ {tool.name}: {tool.description[:60]}...")
            self.log("")
            
        except Exception as e:
            self.operator_ready = False
            self.log(f"[red]✗ Failed to initialize Operator: {e}[/red]")
            self.log("[yellow]Make sure Ollama is running and a model is configured.[/yellow]")
    
    def log(self, message: str) -> None:
        """Log a message to the operator log."""
        log = self.query_one("#operator-log", RichLog)
        log.write(message)
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "operator-send":
            self._run_query()
        elif event.button.id == "operator-clear":
            self.query_one("#operator-log", RichLog).clear()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input field."""
        if event.input.id == "operator-input":
            self._run_query()
    
    def _run_query(self) -> None:
        """Run the operator query."""
        if not self.operator_ready:
            self.log("[red]✗ Operator not ready yet. Please wait...[/red]")
            return
        
        input_field = self.query_one("#operator-input", Input)
        question = input_field.value.strip()
        
        if not question:
            return
        
        input_field.value = ""
        
        # Log the query
        self.log(f"[cyan]→ {question}[/cyan]")
        
        # Run asynchronously to avoid blocking
        asyncio.create_task(self._run_operator_async(question))
    
    async def _run_operator_async(self, question: str) -> None:
        """Run operator in a background task."""
        try:
            # Run in executor to avoid blocking the TUI event loop
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, self.operator.run, question)
            self.log(f"[green]← {answer}[/green]\n")
        except Exception as e:
            self.log(f"[red]✗ Error: {e}[/red]\n")
    
    def refresh_content(self) -> None:
        """Refresh the screen (called by parent app)."""
        # Operator is stateful, refresh just re-logs status
        if self.operator_ready:
            self.log("[cyan]🔄 Refreshed[/cyan]")
