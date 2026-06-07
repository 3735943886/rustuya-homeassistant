"""Thin argparse front-end over DiscoveryManager. All real work lives in the
core package and the manager; this module only parses args and dispatches."""
import os
import sys
import logging
import argparse

from .manager import DiscoveryManager, BrokerUnavailable
from ..core.verifier import CATEGORY_ALIASES
from . import render
from . import backup


def parse_broker(val):
    """Resolve broker host/port: flag > $RUSTUYA_MQTT > localhost:1883."""
    val = val or os.environ.get("RUSTUYA_MQTT") or "localhost:1883"
    if ":" in val:
        host, _, port = val.rpartition(":")
        return host, int(port)
    return val, 1883


def build_parser() -> argparse.ArgumentParser:
    # Config options shared by every command, accepted before OR after the command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--broker", metavar="HOST[:PORT]",
                        help="MQTT broker (or $RUSTUYA_MQTT; default localhost:1883)")
    common.add_argument("--devices", metavar="PATH",
                        help="device list JSON (or $RUSTUYA_DEVICES; default tuyadevices.json)")
    common.add_argument("--converters", metavar="PATH",
                        help="custom converter JSON (or $RUSTUYA_CONVERTERS; default ./custom_converters.json)")
    common.add_argument("--bridge-config", metavar="PATH",
                        help="rustuya-bridge config JSON for topic/payload templates "
                             "(or $RUSTUYA_BRIDGE_CONFIG; else read from retained MQTT, else legacy)")
    common.add_argument("--backup-dir", metavar="PATH",
                        help=f"where publish/clear back up retained discovery "
                             f"(or $RUSTUYA_BACKUP_DIR; default {backup.DEFAULT_DIR})")

    parser = argparse.ArgumentParser(
        prog=render.PROG,
        description="Generate & sync Home Assistant MQTT discovery for rustuya-bridge Tuya devices",
        epilog=render.EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    cat_kwargs = dict(
        action="append", choices=list(CATEGORY_ALIASES.keys()), metavar="CAT",
        help=("Filter to verifier category (repeatable, AND-combined with PATTERN). "
              "Choices: " + ", ".join(CATEGORY_ALIASES) + ". See bottom of -h for meanings."),
    )

    p_status = sub.add_parser("status", parents=[common],
                              help="Show discovery state summary (default)")
    p_status.add_argument("pattern", nargs="?", default="*",
                          help="Filter mismatch detail by id/name fnmatch pattern (default: all)")
    p_status.add_argument("--detail", action="store_true",
                          help="Show field-level diffs for mismatched devices")
    p_status.add_argument("-c", "--category", **cat_kwargs)

    p_prev = sub.add_parser("preview", parents=[common],
                            help="Dump expected generator output for matching devices "
                                 "(mirrors live bridge config if a broker is reachable)")
    p_prev.add_argument("pattern", nargs="?", default="*", help="fnmatch on id/name (default: all)")

    p_pub = sub.add_parser("publish", parents=[common],
                           help="Clear stale + publish current discovery for matching devices")
    p_pub.add_argument("pattern", nargs="?", default="*", help="fnmatch on id/name (default: all)")
    p_pub.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    p_pub.add_argument("--dry-run", action="store_true", help="Show plan without publishing")
    p_pub.add_argument("--no-backup", action="store_true", help="Skip the pre-write backup")
    p_pub.add_argument("-c", "--category", **cat_kwargs)

    p_clr = sub.add_parser("clear", parents=[common],
                           help="Clear discovery topics for matching devices")
    p_clr.add_argument("pattern", nargs="?", default="*", help="fnmatch on id/name (default: all)")
    p_clr.add_argument("--stale-only", action="store_true",
                       help="Only clear topics not produced by current generator")
    p_clr.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    p_clr.add_argument("--dry-run", action="store_true", help="Show plan without clearing")
    p_clr.add_argument("--no-backup", action="store_true", help="Skip the pre-write backup")
    p_clr.add_argument("-c", "--category", **cat_kwargs)

    p_res = sub.add_parser("restore", parents=[common],
                           help="Revert retained discovery to a backup/snapshot (undo publish/clear)")
    p_res.add_argument("file", nargs="?",
                       help="backup file to restore (default: most recent in --backup-dir)")
    p_res.add_argument("--last", action="store_true", help="use the most recent backup (default)")
    p_res.add_argument("--list", dest="list_backups", action="store_true",
                       help="list available backups and exit")
    p_res.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    p_res.add_argument("--dry-run", action="store_true", help="Show plan without writing")
    p_res.add_argument("--no-backup", action="store_true",
                       help="Don't back up the pre-restore state")

    return parser


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)

    host, port = parse_broker(getattr(args, "broker", None))
    devices = getattr(args, "devices", None) or os.environ.get("RUSTUYA_DEVICES") or "tuyadevices.json"
    converters = getattr(args, "converters", None)  # env/CWD/packaged fallback handled in UserConverter
    bridge_config = getattr(args, "bridge_config", None) or os.environ.get("RUSTUYA_BRIDGE_CONFIG")
    backup_dir = (getattr(args, "backup_dir", None) or os.environ.get("RUSTUYA_BACKUP_DIR")
                  or backup.DEFAULT_DIR)

    mgr = DiscoveryManager(broker_host=host, broker_port=port,
                           devices_path=devices, converters_path=converters,
                           bridge_config_path=bridge_config, backup_dir=backup_dir,
                           no_backup=getattr(args, "no_backup", False))

    try:
        if args.cmd in (None, "status"):
            mgr.cmd_status(detail=getattr(args, "detail", False),
                           pattern=getattr(args, "pattern", "*"),
                           categories=getattr(args, "category", None))
        elif args.cmd == "preview":
            mgr.cmd_preview(args.pattern)
        elif args.cmd == "publish":
            mgr.cmd_publish(args.pattern, yes=args.yes, dry_run=args.dry_run, categories=args.category)
        elif args.cmd == "clear":
            mgr.cmd_clear(args.pattern, stale_only=args.stale_only, yes=args.yes,
                          dry_run=args.dry_run, categories=args.category)
        elif args.cmd == "restore":
            mgr.cmd_restore(target=args.file, yes=args.yes, dry_run=args.dry_run,
                            show_list=args.list_backups)
    except BrokerUnavailable as e:
        print(f"❌ {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
