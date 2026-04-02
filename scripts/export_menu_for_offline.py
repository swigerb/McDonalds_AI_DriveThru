#!/usr/bin/env python3
"""Export Azure AI Search menu index to local JSON for offline mode.

Usage:
  python scripts/export_menu_for_offline.py
  python scripts/export_menu_for_offline.py --output ./app/backend/data/offline_menu.json
  python scripts/export_menu_for_offline.py --index my-custom-index

Requires:
  AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY (or Azure credentials)
  in environment or app/backend/.env
"""

import argparse
import json
import os
import sys
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import track
from rich.table import Table

console = Console()


def load_env() -> dict:
    """Load environment variables from .env file and environment."""
    backend_env_path = Path(__file__).parent.parent / "app" / "backend" / ".env"
    if backend_env_path.exists():
        load_dotenv(backend_env_path)

    return {
        "endpoint": os.getenv("AZURE_SEARCH_ENDPOINT"),
        "api_key": os.getenv("AZURE_SEARCH_API_KEY"),
        "index": os.getenv("AZURE_SEARCH_INDEX", "mcdonalds-menu-items"),
    }


def validate_credentials(config: dict) -> bool:
    """Validate required Azure Search credentials."""
    if not config["endpoint"]:
        console.print("[red]Error:[/red] AZURE_SEARCH_ENDPOINT not found in environment or .env")
        return False
    if not config["api_key"]:
        console.print("[red]Error:[/red] AZURE_SEARCH_API_KEY not found in environment or .env")
        return False
    return True


def export_menu(config: dict, output_path: Path) -> bool:
    """Export menu items from Azure Search to JSON file."""
    try:
        console.print(f"[cyan]Connecting to Azure Search...[/cyan]")
        console.print(f"  Endpoint: {config['endpoint']}")
        console.print(f"  Index: {config['index']}")

        client = SearchClient(
            endpoint=config["endpoint"],
            index_name=config["index"],
            credential=AzureKeyCredential(config["api_key"]),
        )

        console.print(f"[cyan]Searching for all documents...[/cyan]")
        results = client.search(
            search_text="*",
            select=["id", "name", "category", "description", "sizes"],
            top=10000,
        )

        items = []
        for doc in track(results, description="Exporting documents..."):
            items.append(doc)

        if not items:
            console.print("[yellow]⚠️  Warning: No documents found in index[/yellow]")
            return False

        output_data = items
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        file_size_kb = output_path.stat().st_size / 1024

        table = Table(title="Export Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Items Exported", str(len(items)))
        table.add_row("File Size", f"{file_size_kb:.2f} KB")
        table.add_row("Output Path", str(output_path))
        table.add_row("Status", "✓ Success")

        console.print(table)
        console.print(f"\n[green]✓ Menu export complete![/green]")

        return True

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        return False


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Export Azure AI Search menu index to local JSON"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "app" / "frontend" / "src" / "data" / "offline_menu.json",
        help="Output file path (default: app/frontend/src/data/offline_menu.json)",
    )
    parser.add_argument(
        "--index",
        type=str,
        help="Override Azure Search index name from environment",
    )

    args = parser.parse_args()

    config = load_env()

    if args.index:
        config["index"] = args.index

    if not validate_credentials(config):
        return 1

    success = export_menu(config, args.output)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
