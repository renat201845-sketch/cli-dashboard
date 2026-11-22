import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def load_config(path: Path) -> dict:
    defaults = {
        "base_currency": "USD",
        "target_currencies": ["EUR", "PLN", "UAH", "GBP"],
        "cache_ttl_minutes": 30,
        "cache_file": "cache.json",
        "export_dir": "exports",
    }
    if not path.exists():
        path.write_text(json.dumps(defaults, indent=2, ensure_ascii=False), encoding="utf-8")
        return defaults
    with path.open("r", encoding="utf-8") as f:
        return {**defaults, **json.load(f)}


def get_cache_file_for_base(cache_path: Path, base_currency: str) -> Path:
    code = base_currency.upper()
    return cache_path.with_name(f"{cache_path.stem}_{code}{cache_path.suffix}")


def fetch_rates(base_currency: str) -> dict:
    url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"
    req = urllib.request.Request(url, headers={"User-Agent": "cli-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("result") != "success" or "rates" not in data:
        raise RuntimeError("API вернул некорректный ответ")
    return {
        "base": base_currency.upper(),
        "fetched_at": int(time.time()),
        "updated_utc": data.get("time_last_update_utc", ""),
        "rates": data["rates"],
    }


def get_rates_with_cache(
    base_currency: str, cache_path: Path, ttl_minutes: int, force_refresh: bool
) -> tuple[dict, bool]:
    ttl_seconds = max(0, ttl_minutes) * 60
    now = int(time.time())
    per_base_cache = get_cache_file_for_base(cache_path, base_currency)

    if not force_refresh and per_base_cache.exists():
        try:
            cached = json.loads(per_base_cache.read_text(encoding="utf-8"))
            age = now - int(cached.get("fetched_at", 0))
            if cached.get("base") == base_currency.upper() and age < ttl_seconds:
                return cached, True
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    fresh = fetch_rates(base_currency)
    try:
        per_base_cache.write_text(json.dumps(fresh, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return fresh, False


def build_rows(payload: dict, targets: list[str], amount: float) -> list[dict]:
    rates = payload["rates"]
    base = payload["base"]
    rows = []
    for code in targets:
        cur = code.upper()
        if cur in rates:
            rate = float(rates[cur])
            rows.append({
                "base": base,
                "currency": cur,
                "rate": round(rate, 4),
                "inverse": round(1 / rate, 4) if rate else 0.0,
                "converted": round(amount * rate, 2),
            })
    return rows


def print_table(
    rows: list[dict], base: str, amount: float, from_cache: bool, updated_utc: str
) -> None:
    source_label = "CACHE" if from_cache else "LIVE API"
    print(f"\n=== Курсы валют | Сумма: {amount:g} {base} | Источник: {source_label} ===")
    if updated_utc:
        print(f"Обновлено API: {updated_utc}")
    print("-" * 60)
    print(f"{'Валюта':<10} {'Курс':>11} {'Обратный':>12} {'Итого':>14}")
    print("-" * 60)
    for r in rows:
        print(
            f"{r['currency']:<10} {r['rate']:>11.4f} {r['inverse']:>12.4f} {r['converted']:>14.2f}"
        )
    print("-" * 60)


def export_data(rows: list[dict], export_dir: Path, fmt: str, base: str) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if fmt == "json":
        out_path = export_dir / f"rates_{base}_{stamp}.json"
        out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    elif fmt == "csv":
        out_path = export_dir / f"rates_{base}_{stamp}.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["base", "currency", "rate", "inverse", "converted"]
            )
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError(f"Неподдерживаемый формат: {fmt}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI Currency Dashboard with cache and export")
    parser.add_argument("--base", help="Базовая валюта (например, EUR, PLN, USD)")
    parser.add_argument("--amount", type=float, default=1.0, help="Сумма для пересчёта (по умолчанию 1)")
    parser.add_argument("--refresh", action="store_true", help="Принудительно обновить данные из API, минуя кэш")
    parser.add_argument("--export", choices=["json", "csv"], help="Сохранить результат в файл (json или csv)")
    args = parser.parse_args()

    if args.amount <= 0:
        print("Сумма --amount должна быть больше нуля.")
        return

    base_dir = Path(__file__).resolve().parent
    config = load_config(base_dir / "config.json")

    base_currency = (args.base or config["base_currency"]).upper()
    targets = config.get("target_currencies", ["EUR", "PLN", "UAH"])
    cache_path = base_dir / config["cache_file"]
    export_dir = base_dir / config["export_dir"]
    ttl_minutes = int(config["cache_ttl_minutes"])

    try:
        payload, from_cache = get_rates_with_cache(
            base_currency=base_currency,
            cache_path=cache_path,
            ttl_minutes=ttl_minutes,
            force_refresh=args.refresh,
        )
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as err:
        print(f"Ошибка при получении курсов: {err}")
        return

    rows = build_rows(payload, targets, args.amount)
    if not rows:
        print("Не найдено ни одной валюты из списка target_currencies.")
        return

    print_table(rows, base_currency, args.amount, from_cache, payload.get("updated_utc", ""))

    if args.export:
        out_file = export_data(rows, export_dir, args.export, base_currency)
        print(f"Экспортировано в: {out_file}")


if __name__ == "__main__":
    main()