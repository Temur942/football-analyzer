"""Командная строка проекта: ручные проходы и проверки.

Запуск:
    python -m football_analyzer.cli fetch-schedule --days 3
    python -m football_analyzer.cli competitions

Здесь нет ничего платного: только бесплатные источники расписания.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

from football_analyzer.analysis.bridge import build_cross_model
from football_analyzer.analysis.forecast import build_forecast, render_text
from football_analyzer.analysis.ratings import MatchRow, fit_rates
from football_analyzer.analysis.service import fit_all_models
from football_analyzer.analysis.weather import forecast_at, geocode
from football_analyzer.competitions import active_competitions
from football_analyzer.config import Settings, get_settings
from football_analyzer.datasources.espn import ScheduleFetch, fetch_schedule
from football_analyzer.datasources.espn_history import discover_season
from football_analyzer.datasources.espn_stats import (
    fetch_summary,
    has_any_statistics,
    parse_summary,
    parse_venue,
)
from football_analyzer.models import Competition
from football_analyzer.scheduler.delivery import deliver_due
from football_analyzer.storage import db

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_EMPTY = 2


def cmd_competitions(args: argparse.Namespace) -> int:
    competitions = active_competitions()
    if args.json:
        print(json.dumps([c.model_dump() for c in competitions], ensure_ascii=False, indent=2))
        return EXIT_OK
    print("=" * 74)
    print("  СОРЕВНОВАНИЯ В РАБОТЕ: %d" % len(competitions))
    print("=" * 74)
    for competition in competitions:
        print(
            "  %-34s %-18s ESPN: %s"
            % (competition.name_ru[:34], competition.country[:18], competition.espn or "—")
        )
    print("=" * 74)
    return EXIT_OK


def cmd_fetch_schedule(args: argparse.Namespace) -> int:
    settings = get_settings()
    competitions = active_competitions()
    if args.competition:
        wanted = set(args.competition)
        competitions = [c for c in competitions if c.id in wanted]
        missing = wanted - {c.id for c in competitions}
        if missing:
            print(
                "ОШИБКА: неизвестные соревнования: %s" % ", ".join(sorted(missing)), file=sys.stderr
            )
            return EXIT_PROBLEM

    result = asyncio.run(
        fetch_schedule(
            competitions,
            days_ahead=args.days,
            days_back=args.back,
            timeout_seconds=settings.http_timeout_seconds,
        )
    )

    if args.json:
        print(
            json.dumps(
                {
                    "fixtures": [
                        {
                            **fixture.model_dump(mode="json"),
                        }
                        for fixture in result.fixtures
                    ],
                    "requests": result.requests,
                    "errors": result.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("=" * 96)
        print(
            "  РАСПИСАНИЕ: %d матчей, %d запросов к источнику"
            % (len(result.fixtures), result.requests)
        )
        print("=" * 96)
        for fixture in result.fixtures:
            print(
                "  %s UTC  %-30s %-28s — %-28s %s"
                % (
                    fixture.kickoff_utc.strftime("%Y-%m-%d %H:%M"),
                    fixture.competition_id[:30],
                    fixture.home_team[:28],
                    fixture.away_team[:28],
                    fixture.status,
                )
            )
        print("=" * 96)
        if result.errors:
            print("  НЕ ПОЛУЧЕНО (%d):" % len(result.errors))
            for error in result.errors[:20]:
                print("    %s" % error)
            if len(result.errors) > 20:
                print("    ... ещё %d" % (len(result.errors) - 20))
            print("=" * 96)

    if result.errors and not result.fixtures:
        return EXIT_PROBLEM
    if not result.fixtures:
        return EXIT_EMPTY
    if result.errors:
        return EXIT_PROBLEM
    return EXIT_OK


async def _migrate(dsn: str) -> list[str]:
    pool = await db.connect(dsn)
    try:
        await db.apply_schema(pool)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
        return [row["tablename"] for row in rows]
    finally:
        await pool.close()


def cmd_migrate(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        tables = asyncio.run(_migrate(settings.require_database_url()))
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА миграции: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    print("=" * 70)
    print("  СХЕМА ПРИМЕНЕНА (idempotent, повторный запуск безопасен)")
    print("=" * 70)
    for table in tables:
        print("  таблица: %s" % table)
    print("=" * 70)
    return EXIT_OK


async def _sync_schedule(
    settings: Settings, competitions: list[Competition], args: argparse.Namespace
) -> tuple[ScheduleFetch, int, int, int]:
    fetch_result = await fetch_schedule(
        competitions,
        days_ahead=args.days,
        days_back=args.back,
        timeout_seconds=settings.http_timeout_seconds,
    )
    pool = await db.connect(settings.require_database_url())
    try:
        await db.apply_schema(pool)
        await db.upsert_competitions(pool, competitions)
        run_id = await db.start_ingest_run(pool, "ESPN", None)
        new_count, updated_count = await db.upsert_fixtures(pool, fetch_result.fixtures)
        await db.finish_ingest_run(
            pool,
            run_id,
            status="ok" if not fetch_result.errors else "partial",
            seen=len(fetch_result.fixtures),
            new_count=new_count,
            updated_count=updated_count,
            error="; ".join(fetch_result.errors[:5]) or None,
        )
        total = await db.count_matches(pool, args.days)
    finally:
        await pool.close()
    return fetch_result, new_count, updated_count, total


def cmd_sync_schedule(args: argparse.Namespace) -> int:
    """Собрать расписание и записать в PostgreSQL."""
    settings = get_settings()
    competitions = active_competitions()
    if args.competition:
        wanted = set(args.competition)
        competitions = [c for c in competitions if c.id in wanted]
        missing = wanted - {c.id for c in competitions}
        if missing:
            print(
                "ОШИБКА: неизвестные соревнования: %s" % ", ".join(sorted(missing)),
                file=sys.stderr,
            )
            return EXIT_PROBLEM

    try:
        fetch_result, new_count, updated_count, total = asyncio.run(
            _sync_schedule(settings, competitions, args)
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА записи в базу: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM

    print("=" * 78)
    print("  СИНХРОНИЗАЦИЯ РАСПИСАНИЯ → PostgreSQL")
    print("=" * 78)
    print(
        "  Получено матчей:        %d (запросов: %d)"
        % (len(fetch_result.fixtures), fetch_result.requests)
    )
    print("  Новых в базе:           %d" % new_count)
    print("  Обновлено:              %d" % updated_count)
    print("  Матчей в базе на окно:  %d (сейчас..+%d дней)" % (total, args.days))
    if fetch_result.errors:
        print("  Ошибок источника:       %d" % len(fetch_result.errors))
        for error in fetch_result.errors[:10]:
            print("    %s" % error)
    print("=" * 78)
    print("  Доказательство: строка в таблице matches, а не лог.")
    if fetch_result.errors and not fetch_result.fixtures:
        return EXIT_PROBLEM
    return EXIT_OK


async def _discover_history(
    dsn: str,
    competitions: list[Competition],
    seasons: list[str],
    pause: float,
    scan_days: bool = False,
) -> list[tuple[str, str, int, str]]:
    """Найти прошедшие матчи за сезоны и записать их в базу."""
    pool = await db.connect(dsn)
    rows: list[tuple[str, str, int, str]] = []
    try:
        await db.apply_schema(pool)
        await db.upsert_competitions(pool, competitions)
        async with aiohttp.ClientSession() as session:
            for competition in competitions:
                for season in seasons:
                    result = await discover_season(
                        session,
                        competition,
                        season,
                        pause_seconds=pause,
                        scan_days=scan_days,
                    )
                    if result.skipped:
                        rows.append((competition.id, season, 0, "пропущено: " + result.skipped))
                        continue
                    new_count, updated = await db.upsert_fixtures(pool, result.fixtures)
                    note = "новых %d, обновлено %d" % (new_count, updated)
                    if result.errors:
                        note += ", ошибок %d" % len(result.errors)
                    rows.append((competition.id, season, len(result.fixtures), note))
    finally:
        await pool.close()
    return rows


def cmd_discover_history(args: argparse.Namespace) -> int:
    settings = get_settings()
    competitions = active_competitions()
    if args.competition:
        wanted = set(args.competition)
        competitions = [c for c in competitions if c.id in wanted]
    try:
        rows = asyncio.run(
            _discover_history(
                settings.require_database_url(),
                competitions,
                args.season,
                args.pause,
                args.scan,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    print("=" * 88)
    print("  ПОИСК ИСТОРИИ: %d соревнований × %d сезонов" % (len(competitions), len(args.season)))
    print("=" * 88)
    for competition_id, season, count, note in rows:
        print("  %-30s %-12s матчей: %-5d %s" % (competition_id[:30], season, count, note[:34]))
    print("=" * 88)
    return EXIT_OK


async def _sync_stats(dsn: str, limit: int, pause: float) -> tuple[int, int, dict[str, int]]:
    pool = await db.connect(dsn)
    done = failed = 0
    try:
        await db.apply_schema(pool)
        pending = await db.pending_stats(pool, limit)
        if pending:
            async with aiohttp.ClientSession() as session:
                for row in pending:
                    try:
                        data = await fetch_summary(
                            session, row["espn_slug"], str(row["source_game_id"])
                        )
                        stats = parse_summary(data, str(row["source_game_id"]))
                        if stats is None or not stats.has_corners_and_cards:
                            if has_any_statistics(data):
                                # Данные есть, но неполные — попробуем позже.
                                await db.mark_stats_error(
                                    pool, row["id"], "в summary нет углов или ЖК"
                                )
                            else:
                                # Источник вообще не хранит статистику матча:
                                # повтор запроса вернёт то же самое.
                                await db.mark_stats_unavailable(
                                    pool, row["id"], "источник не даёт статистику"
                                )
                            failed += 1
                        else:
                            await db.save_stats(pool, row["id"], stats)
                            done += 1
                    except Exception as exc:  # noqa: BLE001
                        await db.mark_stats_error(pool, row["id"], str(exc))
                        failed += 1
                    await asyncio.sleep(pause)
        progress = await db.stats_progress(pool)
    finally:
        await pool.close()
    return done, failed, progress


def cmd_sync_stats(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        done, failed, progress = asyncio.run(
            _sync_stats(settings.require_database_url(), args.limit, args.pause)
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    print("=" * 70)
    print("  СТАТИСТИКА МАТЧЕЙ")
    print("=" * 70)
    print("  Записано сейчас:   %d" % done)
    print("  Неудач сейчас:     %d" % failed)
    print("  Всего матчей:      %d" % progress["total"])
    print("  С статистикой:     %d" % progress["done"])
    print("  Ждёт обработки:    %d" % progress["pending"])
    print("  Не удалось (3×):   %d" % progress["failed"])
    print("=" * 70)
    return EXIT_OK


def to_match_row(row: object) -> MatchRow:
    return MatchRow(
        kickoff_utc=row["kickoff_utc"],  # type: ignore[index]
        home_team=row["home_team"],  # type: ignore[index]
        away_team=row["away_team"],  # type: ignore[index]
        home_goals=row["home_goals"],  # type: ignore[index]
        away_goals=row["away_goals"],  # type: ignore[index]
        home_goals_ht=row["home_goals_ht"],  # type: ignore[index]
        away_goals_ht=row["away_goals_ht"],  # type: ignore[index]
        home_corners=row["home_corners"],  # type: ignore[index]
        away_corners=row["away_corners"],  # type: ignore[index]
        home_yellow=row["home_yellow"],  # type: ignore[index]
        away_yellow=row["away_yellow"],  # type: ignore[index]
    )


async def _sync_venues(dsn: str, limit: int, pause: float) -> tuple[int, int, int]:
    pool = await db.connect(dsn)
    saved = geocoded = 0
    failed = 0
    try:
        await db.apply_schema(pool)
        rows = await db.teams_without_venue(pool, limit)
        async with aiohttp.ClientSession() as session:
            for row in rows:
                try:
                    data = await fetch_summary(session, row["slug"], str(row["source_game_id"]))
                    city, country = parse_venue(data)
                    lat = lon = None
                    if city:
                        coords = await geocode(session, city, country)
                        if coords:
                            lat, lon = coords
                            geocoded += 1
                    await db.upsert_venue(pool, row["team"], city, country, lat, lon)
                    saved += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("стадион не получен для %s: %s", row["team"], exc)
                    failed += 1
                await asyncio.sleep(pause)
    finally:
        await pool.close()
    return saved, geocoded, failed


def cmd_sync_venues(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        saved, geocoded, failed = asyncio.run(
            _sync_venues(settings.require_database_url(), args.limit, args.pause)
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    print("=" * 60)
    print("  СТАДИОНЫ")
    print("=" * 60)
    print("  Обработано команд:   %d" % saved)
    print("  Координаты найдены:  %d" % geocoded)
    print("  Ошибок:              %d" % failed)
    print("=" * 60)
    return EXIT_OK


CUP_PREFIXES = ("uefa-", "int-")


async def _predict(
    dsn: str, days: int, limit: int, weather: bool, competitions: list[str] | None
) -> list[str]:
    """Прогнозы на ближайшие матчи.

    Для лиговых матчей используется модель своей лиги, для еврокубков — общая
    модель со «сшивкой» силы лиг (analysis/bridge.py): иначе силу команды из
    Сегунды нельзя сравнить с командой из АПЛ.
    """
    pool = await db.connect(dsn)
    texts: list[str] = []
    try:
        await db.apply_schema(pool)
        upcoming = await db.upcoming_matches(pool, days)
        if not upcoming:
            return texts

        cross = await fit_all_models(pool)

        async with aiohttp.ClientSession() as session:
            per_competition: dict[str, int] = defaultdict(int)
            for row in upcoming:
                competition_id = row["competition_id"]
                if competitions and competition_id not in competitions:
                    continue
                if per_competition[competition_id] >= limit:
                    continue

                if competition_id.startswith(CUP_PREFIXES):
                    model: object = cross
                else:
                    model = cross.league_rates.get(competition_id)
                if model is None:
                    continue

                home, away = row["home_team"], row["away_team"]
                factor, note = 1.0, None
                if weather:
                    coords = await db.venue_coords(pool, home)
                    if coords:
                        measured = await forecast_at(
                            session, coords[0], coords[1], row["kickoff_utc"]
                        )
                        if measured:
                            factor, note = measured.factor, measured.note

                forecast = build_forecast(
                    model,  # type: ignore[arg-type]
                    competition_id=competition_id,
                    home_team=home,
                    away_team=away,
                    kickoff_utc=row["kickoff_utc"],
                    weather_factor=factor,
                    weather_note=note,
                )
                texts.append(render_text(forecast, row["name_ru"]))
                per_competition[competition_id] += 1
    finally:
        await pool.close()
    return texts


def cmd_predict(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        texts = asyncio.run(
            _predict(
                settings.require_database_url(),
                args.days,
                args.limit,
                args.weather,
                args.competition,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    if not texts:
        print("Нет матчей для прогноза в этом окне (или не хватает истории).")
        return EXIT_EMPTY
    print(("\n" + "-" * 60 + "\n").join(texts))
    return EXIT_OK


async def _backtest(dsn: str, before: datetime, min_train: int) -> list[dict]:
    pool = await db.connect(dsn)
    results: list[dict] = []
    try:
        await db.apply_schema(pool)
        for competition_id in await db.competition_ids(pool):
            rows = [to_match_row(r) for r in await db.finished_matches(pool, competition_id)]
            train = [m for m in rows if m.kickoff_utc < before]
            test = [m for m in rows if m.kickoff_utc >= before]
            if len(train) < min_train or len(test) < 20:
                continue
            rates = fit_rates(train, now=before)
            baseline = fit_rates([], now=before)
            baseline.league_home_goals = rates.league_home_goals
            baseline.league_away_goals = rates.league_away_goals

            def evaluate(
                model: object, _test: list = test, _cid: str = competition_id
            ) -> tuple[float, float, float]:
                hits = 0
                log_loss = 0.0
                brier = 0.0
                for match in _test:
                    forecast = build_forecast(
                        model,  # type: ignore[arg-type]
                        competition_id=_cid,
                        home_team=match.home_team,
                        away_team=match.away_team,
                        kickoff_utc=match.kickoff_utc,
                    )
                    probs = (forecast.p_home, forecast.p_draw, forecast.p_away)
                    actual = (
                        0
                        if match.home_goals > match.away_goals
                        else (1 if match.home_goals == match.away_goals else 2)
                    )
                    if probs.index(max(probs)) == actual:
                        hits += 1
                    log_loss -= math.log(max(probs[actual], 1e-9))
                    over = 1.0 if match.home_goals + match.away_goals > 2.5 else 0.0
                    brier += (forecast.total[2.5] - over) ** 2
                n = len(_test)
                return hits / n, log_loss / n, brier / n

            accuracy, loss, brier = evaluate(rates)
            base_accuracy, base_loss, base_brier = evaluate(baseline)
            results.append(
                {
                    "competition": competition_id,
                    "test": len(test),
                    "accuracy": accuracy,
                    "base_accuracy": base_accuracy,
                    "logloss": loss,
                    "base_logloss": base_loss,
                    "brier": brier,
                    "base_brier": base_brier,
                }
            )
    finally:
        await pool.close()
    return results


def cmd_backtest(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        before = datetime.fromisoformat(args.before).replace(tzinfo=UTC)
    except ValueError:
        print("ОШИБКА: --before в формате YYYY-MM-DD", file=sys.stderr)
        return EXIT_PROBLEM
    try:
        results = asyncio.run(_backtest(settings.require_database_url(), before, args.min_train))
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM

    print("=" * 96)
    print("  БЭКТЕСТ: обучение до %s, проверка после" % args.before)
    print("=" * 96)
    print(
        "  %-24s %6s %9s %9s %9s %9s %8s"
        % ("соревнование", "матчей", "точность", "база", "logloss", "база", "Brier")
    )
    total_test = 0
    for row in results:
        total_test += row["test"]
        print(
            "  %-24s %6d %8.1f%% %8.1f%% %9.4f %9.4f %8.4f"
            % (
                row["competition"][:24],
                row["test"],
                row["accuracy"] * 100,
                row["base_accuracy"] * 100,
                row["logloss"],
                row["base_logloss"],
                row["brier"],
            )
        )
    print("=" * 96)
    if total_test:
        mean_acc = sum(r["accuracy"] * r["test"] for r in results) / total_test
        base_acc = sum(r["base_accuracy"] * r["test"] for r in results) / total_test
        print(
            "  Итог по %d матчам: точность %.1f%% против базы %.1f%%"
            % (total_test, mean_acc * 100, base_acc * 100)
        )
    return EXIT_OK


async def _deliver(dsn: str, lead_hours: int, dry_run: bool) -> tuple[object, dict[str, int]]:
    pool = await db.connect(dsn)
    try:
        await db.apply_schema(pool)
        report = await deliver_due(pool, lead_hours=lead_hours, dry_run=dry_run)
        stats = await db.delivery_stats(pool)
    finally:
        await pool.close()
    return report, stats


def cmd_deliver(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        report, stats = asyncio.run(
            _deliver(settings.require_database_url(), args.lead_hours, not args.send)
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM

    mode = "ПРОБНЫЙ ПРОГОН" if not args.send else "РЕАЛЬНАЯ ОТПРАВКА"
    print("=" * 70)
    print("  РАССЫЛКА ПРОГНОЗОВ (%s)" % mode)
    print("=" * 70)
    print("  Матчей в окне «за %d ч»: %d" % (args.lead_hours, report.due))  # type: ignore[attr-defined]
    print("  Записано в журнал:       %d" % report.dry_run)  # type: ignore[attr-defined]
    print("  Отправлено:              %d" % report.sent)  # type: ignore[attr-defined]
    print("  Пропущено (уже было):    %d" % report.skipped)  # type: ignore[attr-defined]
    print("  Ошибок:                  %d" % report.failed)  # type: ignore[attr-defined]
    print(
        "  Всего в журнале: отправлено %d, пробных %d, ошибок %d"
        % (stats["sent"], stats["dry_run"], stats["failed"])
    )
    print("=" * 70)
    for text in report.texts[:2]:  # type: ignore[attr-defined]
        print(text)
        print("-" * 70)
    return EXIT_OK


async def _backtest_cups(dsn: str, before: datetime, min_train: int) -> list[dict]:
    """Проверить углы и ЖК на еврокубках: калибровка важнее средней точности.

    Считаем Brier — насколько вероятности совпали с фактом (0 = идеально,
    0.25 = «всегда 50%»). Сравниваем с базовой моделью без сшивки лиг.
    """
    pool = await db.connect(dsn)
    rows: list[dict] = []
    try:
        await db.apply_schema(pool)
        competitions = await db.competition_ids(pool)
        league_ids = [c for c in competitions if not c.startswith(CUP_PREFIXES)]
        cup_ids = [c for c in competitions if c.startswith(CUP_PREFIXES)]

        league_matches = {
            cid: [to_match_row(r) for r in await db.finished_matches(pool, cid)]
            for cid in league_ids
        }
        cups = [to_match_row(r) for cid in cup_ids for r in await db.finished_matches(pool, cid)]
        await pool.close()

        train = [m for m in cups if m.kickoff_utc < before]
        test = [m for m in cups if m.kickoff_utc >= before]
        if len(train) < min_train or len(test) < 20:
            return rows

        model = build_cross_model(league_matches, train, now=before)

        lines = {
            "углы 8,5": ("corners", 8.5),
            "углы 9,5": ("corners", 9.5),
            "ЖК 3,5": ("yellow", 3.5),
            "ЖК 4,5": ("yellow", 4.5),
        }
        stats: dict[str, dict[str, float]] = {
            name: {"pred": 0.0, "actual": 0.0, "n": 0} for name in lines
        }
        for match in test:
            forecast = build_forecast(
                model,  # type: ignore[arg-type]
                competition_id="ucl",
                home_team=match.home_team,
                away_team=match.away_team,
                kickoff_utc=match.kickoff_utc,
            )
            corners_total = (match.home_corners or 0) + (match.away_corners or 0)
            yellow_total = (match.home_yellow or 0) + (match.away_yellow or 0)
            for name, (market, line) in lines.items():
                source = (
                    corners_total
                    if market == "corners" and match.home_corners is not None
                    else yellow_total
                    if market == "yellow" and match.home_yellow is not None
                    else None
                )
                if source is None:
                    continue
                probability = (
                    forecast.corners[line] if market == "corners" else forecast.yellow[line]
                )
                outcome = 1.0 if source > line else 0.0
                bucket = stats[name]
                bucket["pred"] += (probability - outcome) ** 2
                bucket["actual"] += (0.5 - outcome) ** 2  # база: «всегда 50%»
                bucket["n"] += 1

        for name, bucket in stats.items():
            if bucket["n"] == 0:
                continue
            rows.append(
                {
                    "market": name,
                    "matches": int(bucket["n"]),
                    "brier": bucket["pred"] / bucket["n"],
                    "baseline": bucket["actual"] / bucket["n"],
                }
            )
    finally:
        pass
    return rows


def cmd_backtest_cups(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        before = datetime.fromisoformat(args.before).replace(tzinfo=UTC)
    except ValueError:
        print("ОШИБКА: --before в формате YYYY-MM-DD", file=sys.stderr)
        return EXIT_PROBLEM
    try:
        rows = asyncio.run(_backtest_cups(settings.require_database_url(), before, args.min_train))
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM

    print("=" * 84)
    print("  БЭКТЕСТ УГЛОВ И ЖК В ЕВРОКУБКАХ (обучение до %s)" % args.before)
    print("=" * 84)
    print("  %-12s %8s %10s %12s %10s" % ("рынок", "матчей", "Brier", "база 50%", "лучше?"))
    for row in rows:
        better = "да" if row["brier"] < row["baseline"] else "нет"
        print(
            "  %-12s %8d %10.4f %12.4f %10s"
            % (row["market"], row["matches"], row["brier"], row["baseline"], better)
        )
    print("=" * 84)
    print("  Brier: меньше — лучше. 0.25 = «всегда 50%».")
    return EXIT_OK


async def _selftest_delivery(dsn: str, lead_hours: int) -> dict[str, int]:
    """Проверить, что два одновременных прохода рассылки не дают дублей.

    Идемпотентность — единственная защита от «прогноз пришёл дважды» после
    перезапуска. Проверяем не рассуждением, а двумя параллельными проходами.
    """
    pool = await db.connect(dsn)
    try:
        await db.apply_schema(pool)
        before = await db.delivery_stats(pool)
        first, second = await asyncio.gather(
            deliver_due(pool, lead_hours=lead_hours, dry_run=True, recipients=[0]),
            deliver_due(pool, lead_hours=lead_hours, dry_run=True, recipients=[0]),
        )
        after = await db.delivery_stats(pool)
        async with pool.acquire() as conn:
            duplicates = await conn.fetchval(
                """
                SELECT count(*) FROM (
                    SELECT match_id, chat_id FROM deliveries
                     GROUP BY 1, 2 HAVING count(*) > 1
                ) AS dupes
                """
            )
    finally:
        await pool.close()
    return {
        "first_written": first.dry_run,
        "second_written": second.dry_run,
        "first_skipped": first.skipped,
        "second_skipped": second.skipped,
        "before": before["dry_run"] + before["sent"],
        "after": after["dry_run"] + after["sent"],
        "duplicates": int(duplicates or 0),
        "due": first.due,
    }


def cmd_selftest_delivery(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        result = asyncio.run(_selftest_delivery(settings.require_database_url(), args.lead_hours))
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM

    print("=" * 70)
    print("  СТРЕСС-ТЕСТ РАССЫЛКИ: два прохода одновременно")
    print("=" * 70)
    print("  Матчей в окне:                 %d" % result["due"])
    print("  Записал первый проход:         %d" % result["first_written"])
    print("  Записал второй проход:         %d" % result["second_written"])
    print("  Пропущено первым (уже было):   %d" % result["first_skipped"])
    print("  Пропущено вторым:              %d" % result["second_skipped"])
    print("  Записей было / стало:          %d / %d" % (result["before"], result["after"]))
    print("  Дубликатов (матч+чат):         %d" % result["duplicates"])
    print("=" * 70)
    if result["duplicates"] == 0:
        print("  ИТОГ: дублей нет — защита работает.")
        return EXIT_OK
    print("  ИТОГ: ОБНАРУЖЕНЫ ДУБЛИ — рассылку включать нельзя.")
    return EXIT_PROBLEM


def load_posts(path: str) -> list[dict]:
    """Прочитать очередь постов. Синхронно — вызывается до работы с базой."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("posts") or []


async def _seed_posts(dsn: str, path: str) -> tuple[int, int]:
    posts = load_posts(path)
    pool = await db.connect(dsn)
    try:
        await db.apply_schema(pool)
        added = await db.add_posts(pool, posts)
        pending = await db.pending_posts(pool)
    finally:
        await pool.close()
    return added, pending


def cmd_seed_posts(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        added, pending = asyncio.run(_seed_posts(settings.require_database_url(), args.file))
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    print("=" * 60)
    print("  ОЧЕРЕДЬ ПУБЛИКАЦИЙ")
    print("=" * 60)
    print("  Добавлено постов:   %d" % added)
    print("  Ждёт публикации:    %d" % pending)
    print("=" * 60)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="football-analyzer", description="Служебные команды")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_competitions = sub.add_parser("competitions", help="список соревнований")
    p_competitions.add_argument("--json", action="store_true")
    p_competitions.set_defaults(func=cmd_competitions)

    p_schedule = sub.add_parser("fetch-schedule", help="получить расписание на окно дат")
    p_schedule.add_argument("--days", type=int, default=3, help="сколько дней вперёд")
    p_schedule.add_argument("--back", type=int, default=0, help="сколько дней назад")
    p_schedule.add_argument(
        "--competition",
        action="append",
        default=None,
        help="только это соревнование (можно несколько)",
    )
    p_schedule.add_argument("--json", action="store_true")
    p_schedule.set_defaults(func=cmd_fetch_schedule)

    p_migrate = sub.add_parser("migrate", help="применить схему БД (idempotent)")
    p_migrate.set_defaults(func=cmd_migrate)

    p_sync = sub.add_parser("sync-schedule", help="собрать расписание и записать в PostgreSQL")
    p_sync.add_argument("--days", type=int, default=3, help="сколько дней вперёд")
    p_sync.add_argument("--back", type=int, default=0, help="сколько дней назад")
    p_sync.add_argument(
        "--competition",
        action="append",
        default=None,
        help="только это соревнование (можно несколько)",
    )
    p_sync.set_defaults(func=cmd_sync_schedule)

    p_history = sub.add_parser("discover-history", help="найти прошедшие матчи за сезоны")
    p_history.add_argument(
        "--season", action="append", required=True, help="сезон, например 2024-2025"
    )
    p_history.add_argument(
        "--competition", action="append", default=None, help="только это соревнование"
    )
    p_history.add_argument("--pause", type=float, default=0.3, help="пауза между запросами")
    p_history.add_argument(
        "--scan", action="store_true", help="перебирать все дни сезона (для кубков)"
    )
    p_history.set_defaults(func=cmd_discover_history)

    p_stats = sub.add_parser("sync-stats", help="догрузить углы и ЖК по прошедшим матчам")
    p_stats.add_argument("--limit", type=int, default=2000, help="сколько матчей за проход")
    p_stats.add_argument("--pause", type=float, default=0.4, help="пауза между запросами")
    p_stats.set_defaults(func=cmd_sync_stats)

    p_venues = sub.add_parser("sync-venues", help="города и координаты стадионов")
    p_venues.add_argument("--limit", type=int, default=400)
    p_venues.add_argument("--pause", type=float, default=0.3)
    p_venues.set_defaults(func=cmd_sync_venues)

    p_predict = sub.add_parser("predict", help="прогноз в процентах на ближайшие матчи")
    p_predict.add_argument("--days", type=int, default=1)
    p_predict.add_argument("--limit", type=int, default=3, help="сколько матчей на лигу")
    p_predict.add_argument("--competition", action="append", default=None)
    p_predict.add_argument("--weather", action="store_true", help="учесть погоду")
    p_predict.set_defaults(func=cmd_predict)

    p_backtest = sub.add_parser("backtest", help="проверка модели на прошедших матчах")
    p_backtest.add_argument("--before", required=True, help="дата отсечки YYYY-MM-DD")
    p_backtest.add_argument("--min-train", type=int, default=300)
    p_backtest.set_defaults(func=cmd_backtest)

    p_deliver = sub.add_parser("deliver", help="разослать прогнозы за N часов до матча")
    p_deliver.add_argument("--lead-hours", type=int, default=5)
    p_deliver.add_argument(
        "--send",
        action="store_true",
        help="реально отправлять (по умолчанию пробный прогон)",
    )
    p_deliver.set_defaults(func=cmd_deliver)

    p_cups = sub.add_parser("backtest-cups", help="бэктест углов и ЖК в еврокубках")
    p_cups.add_argument("--before", required=True, help="дата отсечки YYYY-MM-DD")
    p_cups.add_argument("--min-train", type=int, default=300)
    p_cups.set_defaults(func=cmd_backtest_cups)

    p_selftest = sub.add_parser(
        "selftest-delivery", help="проверить, что два прохода рассылки не дают дублей"
    )
    p_selftest.add_argument("--lead-hours", type=int, default=72)
    p_selftest.set_defaults(func=cmd_selftest_delivery)

    p_seed = sub.add_parser("seed-posts", help="загрузить очередь постов в канал")
    p_seed.add_argument("--file", default="docs/marketing/posts.json")
    p_seed.set_defaults(func=cmd_seed_posts)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, (args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
