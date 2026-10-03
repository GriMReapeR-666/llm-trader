#!/usr/bin/env python3
"""
import logging

logger = logging.getLogger(__name__)
Interactive TUI Menu for LLM Trader
Provides a beautiful interface for configuring trading parameters
"""

import os
import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from typing import Dict, Any, List


# Custom style for questionary
custom_style = Style([
    ('qmark', 'fg:#673ab7 bold'),
    ('question', 'bold'),
    ('answer', 'fg:#f44336 bold'),
    ('pointer', 'fg:#673ab7 bold'),
    ('highlighted', 'fg:#673ab7 bold'),
    ('selected', 'fg:#cc5454'),
    ('separator', 'fg:#cc5454'),
    ('instruction', ''),
    ('text', ''),
])


class InteractiveMenu:
    """Interactive menu for configuring trading parameters"""
    
    def __init__(self):
        self.console = Console()
        self.config = {}
    
    def show_welcome(self):
        """Display welcome banner"""
        welcome_text = """
[bold cyan]╔════════════════════════════════════════════════════════╗[/]
[bold cyan]║[/]  [bold yellow] LLM TRADER - Interactive Configuration[/]     [bold cyan]║[/]
[bold cyan]╚════════════════════════════════════════════════════════╝[/]

[dim]Configure your trading session with this interactive menu.[/]
[dim]All settings can also be provided via CLI arguments.[/]
"""
        self.console.print(Panel(welcome_text, border_style="cyan", box=box.DOUBLE))
    
    def select_mode(self) -> str:
        """Select demo/live/paper mode"""
        mode = questionary.select(
            "▣ Select Trading Mode:",
            choices=[
                questionary.Choice("● Demo Account (Real execution on demo account - RL training enabled)", value="demo"),
                questionary.Choice("● Live Account (Real money - Requires confirmation)", value="live"),
                questionary.Choice("≡ Paper Mode (Simulated execution - No real trades)", value="paper")
            ],
            style=custom_style
        ).ask()
        
        if mode == "live":
            confirm = questionary.confirm(
                "⚠  You selected LIVE mode. Real money will be at risk. Continue?",
                default=False,
                style=custom_style
            ).ask()
            if not confirm:
                self.console.print("[yellow]Switching to Demo mode for safety.[/]")
                return "demo"
        
        return mode
    
    def select_symbols(self) -> str:
        """Select trading symbols with categorization"""
        
        # CATEGORIZED SELECTION
        category = questionary.select(
            "¤ Select Trading Pairs:",
            choices=[
                questionary.Choice(" STARTER WATCHLIST (XAUUSD, EURUSD, GBPJPY)", value="starter"),
                questionary.Choice(" AUTO (LLM selects best from all)", value="auto"),
                questionary.Choice(" Forex Majors", value="forex_majors"),
                questionary.Choice("● Commodities", value="commodities"),
                questionary.Choice("₿  Crypto", value="crypto"),
                questionary.Choice("▣ Indices", value="indices"),
                questionary.Choice("✎  Custom/Manual Input", value="custom")
            ],
            style=custom_style
        ).ask()
        
        if category == "starter":
            return "XAUUSD,EURUSD,GBPJPY"
            
        elif category == "auto":
            self.console.print("\n[cyan] AUTO Mode: LLM will analyze news and market data to select best trading pairs from Broker[/]\n")
            return "AUTO"
            
        elif category == "forex_majors":
            return "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF"
            
        elif category == "commodities":
            return "XAUUSD,XTIUSD,XBRUSD,XAGUSD"
            
        elif category == "crypto":
            return "BTCUSD,ETHUSD,SOLUSD"
            
        elif category == "indices":
            return "US500,USTEC,DE40"
            
        elif category == "custom":
            symbols = questionary.text(
                "Enter symbols (comma-separated, e.g. EURUSD,BTCUSD):",
                default="EURUSD",
                style=custom_style
            ).ask()
            return symbols
            
        return "AUTO"

    def select_timeframe(self) -> str:
        """Enforce AUTO timeframe (User request)"""
        # Automatically return AUTO, but maybe notify user
        # self.console.print("\n[cyan]  Timeframe: Set to AUTO for Multi-Timeframe Analysis[/]\n")
        return "AUTO"

    
    def select_model(self) -> str:
        """Select AI Provider and Model"""
        
        # Step 1: Select Provider
        provider = questionary.select(
            " Select AI Provider:",
            choices=[
                questionary.Choice("◎ OpenRouter (Aggregator - GPT4, Claude 3.5, Mistral, etc.)", value="openrouter"),
                questionary.Choice(" Cerebras AI (World's Fastest Inference - Free Llama 3.1 & 3.3)", value="cerebras")
            ],
            style=custom_style
        ).ask()
        
        if provider == "cerebras":
            return self._select_cerebras_model()
        else:
            return self._select_openrouter_model()

    def configure_llm_providers_and_models(self) -> Dict[str, Any]:
        """Checkbox select providers, then select a model for each selected provider"""
        self.console.print("\n[cyan]□ LLM Provider & Model Selection[/cyan]")
        self.console.print("[dim]Choose which API providers to use, then pick a model for each.[/dim]\n")
        
        available = []
        
        if os.getenv("OPENROUTER_API_KEY"):
            available.append(questionary.Choice("◎ OpenRouter", value="openrouter", checked=True))
        else:
            available.append(questionary.Choice("◎ OpenRouter (no key)", value="openrouter", checked=False))
        
        if os.getenv("CEREBRAS_API_KEY"):
            available.append(questionary.Choice(" Cerebras", value="cerebras", checked=True))
        else:
            available.append(questionary.Choice(" Cerebras (no key)", value="cerebras", checked=False))
        
        nvidia_keys = []
        i = 1
        while True:
            key = os.getenv(f"NVIDIA_KEY_{i}") or (os.getenv("NVIDIA_API_KEY") if i == 1 else None)
            if not key:
                break
            nvidia_keys.append(key)
            i += 1
        
        for idx in range(1, len(nvidia_keys) + 1):
            available.append(questionary.Choice(f"● NVIDIA NIM #{idx}", value=f"nvidia_{idx}", checked=True))
        
        if not available:
            self.console.print("[yellow]No LLM providers detected in environment.[/yellow]")
            return {"provider_models": {}, "providers": []}
        
        selected = questionary.checkbox(
            "Select providers to use (space to toggle, enter to confirm):",
            choices=available,
            style=custom_style
        ).ask()
        
        if selected is None or not selected:
            return {"provider_models": {}, "providers": []}
        
        provider_models = {}
        for provider in selected:
            model = self._select_model_for_provider(provider)
            if model:
                provider_models[provider] = model
        
        self.console.print(f"\n[green]✔ Configured {len(provider_models)} provider(s)[/green]\n")
        for prov, model in provider_models.items():
            self.console.print(f"  [dim]{prov}[/dim] -> [yellow]{model}[/yellow]")
        self.console.print("")
        
        return {
            "provider_models": provider_models,
            "providers": list(provider_models.keys())
        }

    def _select_model_for_provider(self, provider: str) -> str:
        """Select a model for a specific provider"""
        if provider == "openrouter":
            return self._select_openrouter_model()
        elif provider == "cerebras":
            return self._select_cerebras_model()
        elif provider.startswith("nvidia_"):
            idx = provider.split("_")[-1]
            return self._select_nvidia_model(idx)
        return ""

    def _select_nvidia_model(self, idx: str = "1") -> str:
        """Select NVIDIA NIM Model"""
        choices = [
            questionary.Choice("Llama 3.1 70B Instruct", value="nvidia/llama-3.1-70b-instruct"),
            questionary.Choice("Llama 3.2 11B Vision Instruct", value="meta/llama-3.2-11b-vision-instruct"),
            questionary.Choice("GPT-OSS 20B", value="openai/gpt-oss-20b"),
        ]
        model = questionary.select(
            f"● Select NVIDIA NIM #{idx} Model:",
            choices=choices,
            style=custom_style
        ).ask()
        return model or ""

    def configure_llm_providers(self) -> Dict[str, Any]:
        """Backward compatibility wrapper"""
        return self.configure_llm_providers_and_models()

    def _select_cerebras_model(self) -> str:
        """Select Cerebras Model (Dynamic Fetch)"""
        self.console.print("\n[cyan]Fetching available models from Cerebras...[/]")
        
        models = self._fetch_cerebras_models()
        
        if not models:
            self.console.print("[yellow]Could not fetch Cerebras models (Check API Key). Using known list.[/]")
            choices = [
                questionary.Choice(" GPT-OSS 120B (billing-blocked)", value="gpt-oss-120b"),
                questionary.Choice("← Back to Provider Selection", value="back")
            ]
        else:
            # Sort models
            models.sort(key=lambda x: x['id'])
            choices = [questionary.Choice(f"{m['id']} (Free Tier)", value=f"cerebras/{m['id']}") for m in models]
            choices.append(questionary.Choice("← Back to Provider Selection", value="back"))

        model = questionary.select(
            " Select Cerebras Model:",
            choices=choices,
            style=custom_style
        ).ask()
        
        if model == "back":
            return self.select_model()
            
        return model

    def _fetch_cerebras_models(self) -> List[Dict[str, Any]]:
        """Fetch models from Cerebras API"""
        try:
            import requests
            api_key = os.getenv("CEREBRAS_API_KEY")
            if not api_key:
                return []
            
            # Cerebras is OpenAI compatible
            response = requests.get(
                "https://api.cerebras.ai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('data', [])
        except Exception as exc:
            logger.debug("take-profit failed: %s", exc)
        return []

    def _check_openrouter_status(self) -> None:
        """Check OpenRouter limits and credit status"""
        try:
            import requests
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                return
                
            response = requests.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json().get('data', {})
                usage = data.get('usage', 0)
                limit = data.get('limit')
                remaining = data.get('limit_remaining')
                is_free = data.get('is_free_tier', False)
                
                # Format message
                status_msg = f"[bold]▣ OpenRouter Wallet:[/bold]\n"
                status_msg += f"• Tier: [magenta]{'Free' if is_free else 'Paid'}[/magenta]\n"
                status_msg += f"• Usage: [yellow]${float(usage):.4f}[/yellow]"
                
                if limit is not None:
                    status_msg += f" / Limit: ${float(limit):.4f}"
                
                if remaining is not None:
                     rem_val = float(remaining)
                     color = "red" if rem_val <= 0 else "green"
                     status_msg += f"\n• Credits Remaining: [{color}]${rem_val:.4f}[/{color}]"
                
                if is_free:
                     status_msg += "\n• [dim]Limits: 200 RPM / 200 RPD (if <$5 credits)[/dim]"
                     status_msg += "\n• [dim]Warning: Negative balance blocks free models (402 Error)[/dim]"
                
                self.console.print(Panel(status_msg, border_style="cyan", box=box.ROUNDED))
                
        except Exception as exc:
            logger.debug("balance check failed: %s", exc)


    def _select_openrouter_model(self) -> str:
        """Select OpenRouter Model with Free/Paid categorization"""
        self._check_openrouter_status()
        self.console.print("\n[cyan]Fetching available models from OpenRouter...[/]")
        
        # Try to fetch models from OpenRouter
        models = self._fetch_openrouter_models()
        
        if not models:
            # Fallback to predefined list
            self.console.print("[yellow]Could not fetch models. Using fallback list.[/]")
            models = [
                {"name": "Mistral Nemo (Free)", "value": "mistralai/mistral-nemo", "is_free": True},
                {"name": "Llama 3.3 70B (Free)", "value": "meta-llama/llama-3.3-70b-instruct", "is_free": True},
                {"name": "Qwen3 8B (Free)", "value": "qwen/qwen3-8b", "is_free": True},
                {"name": "GPT-4 Turbo (Paid)", "value": "openai/gpt-4-turbo", "is_free": False},
            ]
        
        # Split into Free and Paid
        free_models = [m for m in models if m.get("is_free")]
        paid_models = [m for m in models if not m.get("is_free")]
        
        # Step 1: Select Category
        category = questionary.select(
            "◎ Select OpenRouter Category:",
            choices=[
                questionary.Choice(f" Free Models ({len(free_models)} available)", value="free"),
                questionary.Choice(f" Paid Models ({len(paid_models)} available)", value="paid"),
                questionary.Choice("✎  Custom Model ID", value="custom"),
                questionary.Choice("← Back to Provider Selection", value="back")
            ],
            style=custom_style
        ).ask()
        
        if category == "back":
            return self.select_model()
            
        if category == "custom":
            return questionary.text(
                "Enter custom model ID (e.g., 'mistralai/mistral-nemo'):",
                style=custom_style
            ).ask()
            
        # Step 2: Select Model from Category
        target_list = free_models if category == "free" else paid_models
        
        # Sort by name
        target_list.sort(key=lambda x: x["name"])
        
        choices = [questionary.Choice(m["name"], value=m["value"]) for m in target_list]
        choices.append(questionary.Choice("← Back to Categories", value="back"))
        
        model = questionary.select(
            f"Select {category.capitalize()} Model:",
            choices=choices,
            style=custom_style
        ).ask()
        
        if model == "back":
            return self._select_openrouter_model() # Recursive call to go back
            
        return model
    
    def _fetch_openrouter_models(self) -> List[Dict[str, Any]]:
        """Fetch available models from OpenRouter API"""
        try:
            import requests
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                return []
            
            response = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                models = []
                
                for model in data.get('data', []):
                    pricing = model.get('pricing', {})
                    prompt_price = float(pricing.get('prompt', '0'))
                    completion_price = float(pricing.get('completion', '0'))
                    
                    is_free = (prompt_price == 0 and completion_price == 0)
                    
                    # Format price string
                    if is_free:
                        price_str = "FREE"
                    else:
                        price_per_1m = prompt_price * 1_000_000
                        price_str = f"${price_per_1m:.2f}/1M"
                    
                    name = model.get('name', model['id'])
                    models.append({
                        "name": f"{name} ({price_str})",
                        "value": model['id'],
                        "is_free": is_free,
                        "price": prompt_price
                    })
                
                return models
        except Exception as e:
            self.console.print(f"[dim]Could not fetch models: {e}[/]")
            return []
    
    def select_trading_mode(self) -> Dict[str, Any]:
        """Select single/continuous mode"""
        mode = questionary.select(
            "↻ Select Trading Mode:",
            choices=[
                questionary.Choice("Single Cycle (Run once or N times)", value="single"),
                questionary.Choice("Continuous (24/7 automated trading)", value="continuous")
            ],
            style=custom_style
        ).ask()
        
        if mode == "single":
            loops = questionary.text(
                "Number of cycles:",
                default="1",
                validate=lambda x: x.isdigit() and int(x) > 0,
                style=custom_style
            ).ask()
            
            return {"mode": "single", "loops": int(loops)}
        else:
            confirm = questionary.confirm(
                "⚠  Continuous mode will run 24/7. Ensure proper monitoring. Continue?",
                default=True,
                style=custom_style
            ).ask()
            
            if confirm:
                return {"mode": "continuous", "loops": None}
            else:
                return {"mode": "single", "loops": 1}
    
    def configure_risk(self) -> Dict[str, float]:
        """Configure risk parameters"""
        risk_per_trade = questionary.text(
            "$ Risk per Trade (%):",
            default="1.0",
            validate=lambda x: self._validate_float(x, 0.1, 5.0),
            style=custom_style
        ).ask()
        
        # The UI accepts a human percentage ("1.0" = one percent); the
        # execution engine always receives a fraction (0.01).
        return {"risk_per_trade": float(risk_per_trade) / 100.0}
    
    def _validate_float(self, value: str, min_val: float, max_val: float) -> bool:
        """Validate float input"""
        try:
            val = float(value)
            return min_val <= val <= max_val
        except:
            return False
    
    def configure_advanced(self) -> Dict[str, bool]:
        """Configure advanced options"""
        show_advanced = questionary.confirm(
            "□  Configure advanced options?",
            default=False,
            style=custom_style
        ).ask()
        
        if not show_advanced:
            return {"show_metrics": False}
        
        show_metrics = questionary.confirm(
            "Show detailed metrics at end?",
            default=True,
            style=custom_style
        ).ask()
        
        show_rl_metrics = questionary.confirm(
            "Show RL Training Metrics?",
            default=True,
            style=custom_style
        ).ask()
        
        return {"show_metrics": show_metrics, "show_rl_metrics": show_rl_metrics}

    def configure_tui(self) -> bool:
        """Ask whether to enable the complex TUI Dashboard"""
        return questionary.confirm(
            "□  Enable complex TUI Dashboard? (Select 'No' for simple colorful CLI)",
            default=False,
            style=custom_style
        ).ask()
    
    def show_summary(self, config: Dict[str, Any]) -> bool:
        """Display configuration summary and confirm"""
        table = Table(title="≡ Configuration Summary", box=box.ROUNDED, show_header=False)
        table.add_column("Setting", style="cyan", width=20)
        table.add_column("Value", style="yellow")
        
        table.add_row("Trading Mode", config['mode'].upper())
        table.add_row("Symbol(s)", config['symbols'])
        
        # Show AUTO timeframe with description
        if config['timeframe'] == "AUTO":
            table.add_row("Timeframe", "AUTO (D1→M30 analysis)")
        else:
            table.add_row("Timeframe", config['timeframe'])
        
        table.add_row("LLM Model", config['model'])
        llm_config = config.get('llm_config', {})
        provider_models = llm_config.get('provider_models', {})
        if provider_models:
            models_str = ", ".join([f"{p}: {m}" for p, m in provider_models.items()])
            table.add_row("LLM Providers", f"{len(provider_models)} selected")
            table.add_row("LLM Models", models_str)
        else:
            table.add_row("LLM Providers", "All available")
        
        if config['trading_mode']['mode'] == 'continuous':
            table.add_row("Execution Mode", "↻ CONTINUOUS (24/7)")
        else:
            table.add_row("Execution Mode", f"Single ({config['trading_mode']['loops']} cycles)")
        
        table.add_row("Risk per Trade", f"{config['risk']['risk_per_trade'] * 100:.2f}%")
        
        table.add_row("Interface", "Complex Dashboard" if config.get('tui') else "Simple Colorful CLI")
        
        self.console.print("\n")
        self.console.print(table)
        self.console.print("\n")
        
        return questionary.confirm(
            " Start trading with these settings?",
            default=True,
            style=custom_style
        ).ask()
    
    def run(self) -> Dict[str, Any]:
        """Run the interactive menu and return configuration"""
        self.show_welcome()
        
        try:
            # Step-by-step configuration
            self.config['mode'] = self.select_mode()
            if self.config['mode'] is None:
                self.console.print("\n[yellow] Menu cancelled by user.[/]\n")
                return None
            
            self.config['symbols'] = self.select_symbols()
            if self.config['symbols'] is None:
                self.console.print("\n[yellow] Menu cancelled by user.[/]\n")
                return None
            
            # Timeframe selection
            timeframe_mode = questionary.select(
                "  Timeframe Selection:",
                choices=[
                    questionary.Choice(" AUTO (Analyze multiple timeframes)", value="AUTO"),
                    questionary.Choice("Manual Selection", value="manual")
                ],
                style=custom_style
            ).ask()
            
            if timeframe_mode == "manual":
                self.config['timeframe'] = self.select_timeframe()
            else:
                self.console.print("\n[cyan]▣ Timeframe: AUTO (analyzing D1, H4, H1, M30 for best setup)[/]\n")
                self.config['timeframe'] = "AUTO"
            
            self.config['llm_config'] = self.configure_llm_providers_and_models()
            provider_models = self.config.get('llm_config', {}).get('provider_models', {})
            if provider_models:
                self.config['model'] = list(provider_models.values())[0]
            else:
                self.config['model'] = self.select_model()
            if self.config['model'] is None:
                self.console.print("\n[yellow] Menu cancelled by user.[/]\n")
                return None
            
            self.config['trading_mode'] = self.select_trading_mode()
            if self.config['trading_mode'] is None:
                self.console.print("\n[yellow] Menu cancelled by user.[/]\n")
                return None
            
            self.config['risk'] = self.configure_risk()
            if self.config['risk'] is None:
                self.console.print("\n[yellow] Menu cancelled by user.[/]\n")
                return None
            
            self.config['advanced'] = self.configure_advanced()
            if self.config['advanced'] is None:
                self.console.print("\n[yellow] Menu cancelled by user.[/]\n")
                return None
            
            self.config['tui'] = self.configure_tui()
            
            # Show summary and confirm
            if self.show_summary(self.config):
                self.console.print("\n[bold green]✔ Configuration complete! Starting trading...[/]\n")
                return self.config
            else:
                self.console.print("\n[yellow] Trading cancelled by user.[/]\n")
                return None
        
        except KeyboardInterrupt:
            self.console.print("\n\n[yellow] Menu cancelled by user (Ctrl+C).[/]\n")
            return None


if __name__ == "__main__":
    # Test the menu
    menu = InteractiveMenu()
    config = menu.run()
    
    if config:
        print("\nFinal Configuration:")
        import json
        print(json.dumps(config, indent=2))
        
        # Construct CLI Command
        cmd = ["python", "mt5_trader.py", "llm-trade"]
        
        # Mode
        if config['mode'] == "paper":
            cmd.append("--paper")
        elif config['mode'] == "live":
            cmd.append("--live")
        else:
            cmd.append("--demo")
            
        # Symbols
        cmd.extend(["--symbol", config['symbols']])
        
        # Model
        if config.get('model'):
            cmd.extend(["--model", config['model']])
        
        # LLM Providers and Models
        llm_config = config.get('llm_config', {})
        provider_models = llm_config.get('provider_models', {})
        if provider_models:
            providers = list(provider_models.keys())
            models = [f"{p}:{provider_models[p]}" for p in providers]
            cmd.extend(["--llm-providers", ",".join(providers)])
            cmd.extend(["--llm-models", ",".join(models)])
            
        # Loops/Continuous
        if config['trading_mode']['mode'] == "continuous":
             cmd.append("--continuous")
        else:
             cmd.extend(["--loops", str(config['trading_mode']['loops'])])
             
        # TUI
        if config.get('tui'):
            cmd.append("--tui")
            
        print(f"\n Launching Trader: {' '.join(cmd)}\n")
        
        import subprocess
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\n■ Trading Session Terminated.")
