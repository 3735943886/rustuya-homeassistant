import json
import re
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Tuple

try:
    import yaml
except:
    pass

logger = logging.getLogger("external_converter")

class ConverterConfig:
    # Use generic directory for external converters
    BASE_PATH = Path("external_converter")
    YAML_PATH = BASE_PATH
    TS_PATH = BASE_PATH

    CUSTOM_CONVERTER_PATH = "custom_converters.json"
    CACHE_FILE = BASE_PATH / "yaml_cache.json"

    # regex patterns for TS parsing
    RE_PID_TZ = r'["\'](_TZ.*?)["\']'
    RE_PID_TZE = r'["\'](_TZE.*?)["\']'
    RE_TS_BLOCK_SPLIT = r'\n\s*},\s*\n\s*{\s*\n'
    RE_DP_MATCH = r'\[(\d+),\s*["\'](.*?)["\']'

class CustomConverter:
    def __init__(self, path: str = ConverterConfig.CUSTOM_CONVERTER_PATH):
        self.path = Path(path)
        self.mapping: Dict[str, Any] = {}

    def load(self):
        if not self.path.exists(): return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                self.mapping = json.load(f)
        except Exception as e:
            logger.error(f"Error loading custom converters: {e}")

class YamlScanner:
    def __init__(self, devices_path: Path = ConverterConfig.YAML_PATH):
        self.devices_path = devices_path
        self.product_map: Dict[str, Any] = {}
        self.cache_path = ConverterConfig.CACHE_FILE

    def scan(self, force: bool = False):
        if not force and self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    self.product_map = json.load(f)
                    if self.product_map: return
            except Exception:
                pass

        if not self.devices_path.exists():
            logger.info(f"YAML converter path not found: {self.devices_path}")
            return

        files = list(self.devices_path.rglob("*.yaml"))
        if not files: return

        with ThreadPoolExecutor() as executor:
            results = list(executor.map(self._parse_file, files))

        for res in filter(None, results):
            pids, data = res
            for pid in pids:
                self.product_map[pid] = data

        if self.product_map:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_path, 'w') as f:
                    json.dump(self.product_map, f)
            except Exception:
                pass

    def _parse_file(self, file_path: Path) -> Optional[Tuple[List[str], Any]]:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) if yaml else None
                if not data or 'products' not in data: return None

                pids = [p.get('id') for p in data['products'] if p.get('id')]
                if not pids: return None

                # Extract rich metadata for each DP
                dp_meta = {}
                for entity in data.get('entities', []):
                    comp = entity.get('entity')
                    dev_cls = entity.get('class')
                    icon = entity.get('icon')

                    for dp in entity.get('dps', []):
                        dp_id = str(dp.get('id'))
                        info = {
                            "comp": comp,
                            "class": dev_cls or dp.get('class'),
                            "unit": dp.get('unit'),
                            "icon": icon or dp.get('icon'),
                        }
                        # Range / Scale / Step
                        for k in ['scale', 'min', 'max', 'step']:
                            if k in dp: info[k] = dp[k]

                        # Mapping (Options / Enum)
                        if 'mapping' in dp:
                            mappings = dp['mapping']
                            options = []
                            val_map = {}
                            for m in mappings:
                                if 'dps_val' in m and 'value' in m:
                                    options.append(m['value'])
                                    val_map[str(m['dps_val'])] = m['value']
                                # Scale can also be inside mapping in some versions
                                for k in ['scale', 'min', 'max', 'step']:
                                    if k in m: info[k] = m[k]

                            if options: info["options"] = options
                            if val_map: info["val_map"] = val_map

                        dp_meta[dp_id] = info

                return pids, {"name": data.get('name'), "dp_meta": dp_meta}
        except Exception:
            pass
        return None

class TsScanner:
    def __init__(self, ts_path: Path = ConverterConfig.TS_PATH):
        self.ts_path = ts_path
        self.fingerprint_map: Dict[str, Any] = {}
        self.model_map: Dict[str, Any] = {}

    def scan(self):
        if not self.ts_path.exists():
            logger.info(f"TS converter path not found: {self.ts_path}")
            return

        files = list(self.ts_path.rglob("*.ts"))
        for file in files:
            self._parse_file(file)

    def _parse_file(self, file_path: Path):
        try:
            content = file_path.read_text(encoding='utf-8')
            blocks = re.split(ConverterConfig.RE_TS_BLOCK_SPLIT, content)
            for block in blocks:
                if 'model:' not in block: continue

                pids = self._extract_pids(block)
                if not pids: continue

                model_match = re.search(r'model:\s*["\'](.*?)["\']', block)
                if model_match:
                    model = model_match.group(1)
                    vendor_match = re.search(r'vendor:\s*["\'](.*?)["\']', block)
                    vendor = vendor_match.group(1) if vendor_match else "Tuya"

                    dp_map = {m[0]: m[1] for m in re.findall(ConverterConfig.RE_DP_MATCH, block)}
                    info = {"model": model, "vendor": vendor, "dp_map": dp_map}

                    for pid in pids: self.fingerprint_map[pid] = info
                    self.model_map[model] = info
        except Exception as e:
            logger.debug(f"Error scanning TS file {file_path}: {e}")

    def _extract_pids(self, block: str) -> List[str]:
        pids = []
        for fp in re.findall(r'tuya\.fingerprint\(".*?", \[(.*?)\]\)', block, re.DOTALL):
            pids.extend(re.findall(ConverterConfig.RE_PID_TZ, fp))
            pids.extend(re.findall(ConverterConfig.RE_PID_TZE, fp))
        zm = re.search(r'zigbeeModel:\s*\[(.*?)\]', block, re.DOTALL)
        if zm: pids.extend(re.findall(r'["\'](.*?)["\']', zm.group(1)))
        fm = re.search(r'fingerprint:\s*\[(.*?)\]', block, re.DOTALL)
        if fm: pids.extend(re.findall(r'manufacturerName:\s*["\'](_TZ.*?)["\']', fm.group(1)))
        return [p.split('_')[-1] if '_' in p else p for p in pids]

class ExternalConverter:
    def __init__(self, yaml_path: Optional[str] = None,
                 ts_path: Optional[str] = None,
                 custom_path: Optional[str] = None,
                 force_update: bool = False):

        self.custom = CustomConverter(custom_path or ConverterConfig.CUSTOM_CONVERTER_PATH)
        self.yaml_scanner = YamlScanner(Path(yaml_path) if yaml_path else ConverterConfig.YAML_PATH)
        self.ts_scanner = TsScanner(Path(ts_path) if ts_path else ConverterConfig.TS_PATH)

        self.custom.load()
        self.yaml_scanner.scan(force=force_update)
        self.ts_scanner.scan()

    def find_match(self, product_id: str):
        """Find match in external sources. Returns (source_name, info_dict) or (None, None)."""
        # 0. Custom Converter (Highest Priority)
        if custom_info := self.custom.mapping.get(product_id):
            return "custom", custom_info

        # 1. YAML Match
        if yaml_info := self.yaml_scanner.product_map.get(product_id):
            return "yaml", yaml_info

        # 2. TS Match
        ts_info = self.ts_scanner.fingerprint_map.get(product_id) or self.ts_scanner.model_map.get(product_id)
        if ts_info:
            return "ts", ts_info

        return None, None
