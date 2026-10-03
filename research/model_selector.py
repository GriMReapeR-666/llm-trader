#!/usr/bin/env python3
"""
Model Selection Menu - Interactive TUI for choosing LLM models
Supports OpenRouter API models and local models
"""

import os
import requests
from typing import List, Dict, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box


console = Console()


class ModelSelector:
    """Interactive model selection with keyboard navigation"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.available_models: List[Dict[str, str]] = []
        self.local_models: List[Dict[str, str]] = []
    
    def fetch_openrouter_models(self) -> List[Dict[str, str]]:
        """Fetch available models from OpenRouter API"""
        if not self.api_key:
            console.print("[yellow]⚠️  No OpenRouter API key found[/yellow]")
            return []
        
        try:
            response = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                models = []
                
                for model in data.get("data", []):
                    models.append({
                        "id": model["id"],
                        "name": model.get("name", model["id"]),
                        "context_length": model.get("context_length", 0),
                        "pricing": model.get("pricing", {})
                    })
                
                console.print(f"[green]✅ Fetched {len(models)} models from OpenRouter[/green]")
                return models
            else:
                console.print(f"[red]❌ OpenRouter API error: {response.status_code}[/red]")
                return []
                
        except Exception as e:
            console.print(f"[red]❌ Failed to fetch models: {e}[/red]")
            return []
    
    def get_local_models(self) -> List[Dict[str, str]]:
        """Get list of local models (placeholder for future implementation)"""
        return [
            {"id": "local/llama-3.1-8b", "name": "Llama 3.1 8B (Local)", "context_length": 8192},
            {"id": "local/mistral-7b", "name": "Mistral 7B (Local)", "context_length": 8192}
        ]
    
    def display_model_table(self, models: List[Dict[str, str]], selected_idx: int = 0):
        """Display models in a formatted table with selection highlight"""
        table = Table(title="Available LLM Models", box=box.ROUNDED, show_header=True)
        
        table.add_column("#", style="dim", width=4)
        table.add_column("Model Name", style="cyan")
        table.add_column("Model ID", style="dim")
        table.add_column("Context", justify="right", style="magenta")
        
        for idx, model in enumerate(models):
            marker = "→" if idx == selected_idx else " "
            style = "bold green" if idx == selected_idx else ""
            
            context_len = model.get("context_length", 0)
            context_str = f"{context_len:,}" if context_len > 0 else "N/A"
            
            table.add_row(
                f"{marker} {idx+1}",
                model.get("name", "Unknown"),
                model.get("id", ""),
                context_str,
                style=style
            )
        
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]Model Selection Menu[/bold cyan]\n" +
            "Use [yellow]↑/↓[/yellow] arrows or enter number to select\n" +
            "Press [yellow]Enter[/yellow] to confirm, [yellow]Q[/yellow] to quit",
            border_style="blue"
        ))
        console.print(table)
    
    def select_model_interactive(self) -> Optional[str]:
        """
        Interactive model selection with arrow key navigation
        
        Returns:
            Selected model ID or None if cancelled
        """
        # Fetch available models
        with console.status("[bold green]Fetching available models..."):
            openrouter_models = self.fetch_openrouter_models()
            local_models = self.get_local_models()
        
        all_models = openrouter_models + local_models
        
        if not all_models:
            console.print("[red]❌ No models available[/red]")
            return None
        
        # For simplicity, use Rich's Prompt for now (simple-term-menu has Windows issues)
        # Display table
        self.display_model_table(all_models)
        
        # Get selection
        choice = Prompt.ask(
            "\n[bold yellow]Enter model number[/bold yellow]",
            default="1",
            show_default=True
        )
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(all_models):
                selected = all_models[idx]
                console.print(f"\n[green]✅ Selected: {selected['name']}[/green]")
                return selected["id"]
            else:
                console.print("[red]❌ Invalid selection[/red]")
                return None
        except ValueError:
            console.print("[red]❌ Invalid input[/red]")
            return None


def test_model_selector():
    """Test the model selector"""
    selector = ModelSelector()
    selected_model = selector.select_model_interactive()
    
    if selected_model:
        console.print(f"\n[bold green]You selected: {selected_model}[/bold green]")
    else:
        console.print("\n[yellow]No model selected[/yellow]")


if __name__ == "__main__":
    test_model_selector()
