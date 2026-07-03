# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, importlib, os, sys  # isort: skip
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import certifi

from . import ad_loading, ad_status, delete_flow, download_flow, extend_flow
from . import login_flow as _login_flow
from . import publishing_workflow as _publishing_workflow
from . import runtime_config as _runtime_config
from . import update_checker as _update_checker
from ._version import __version__
from .login_flow import LoginDetectionResult
from .model.ad_model import Ad, AdUpdateStrategy
from .model.config_model import Config  # noqa: TC001 — used at runtime, config injection
from .published_ads import PublishedAd
from .utils import color as _color
from .utils import diagnostics as _diagnostics
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils import xdg_paths as _xdg_paths
from .utils.files import abspath
from .utils.misc import is_frozen
from .utils.web_scraping_mixin import WebScrapingMixin

if TYPE_CHECKING:
    from .utils.timing_collector import TimingCollector

# W0406: possibly a bug, see https://github.com/PyCQA/pylint/issues/3933

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)


class KleinanzeigenBot(WebScrapingMixin):  # noqa: PLR0904
    def __init__(self) -> None:
        # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/295
        # see https://github.com/pyinstaller/pyinstaller/issues/7229#issuecomment-1309383026
        os.environ["SSL_CERT_FILE"] = certifi.where()

        super().__init__()

        self.root_url = "https://www.kleinanzeigen.de"

        self.config:Config
        self.config_file_path = abspath("config.yaml")
        self.workspace:_xdg_paths.Workspace | None = None
        self._config_arg:str | None = None
        self._workspace_mode_arg:_xdg_paths.InstallationMode | None = None

        self.categories:dict[str, str] = {}

        self.file_log:_loggers.LogFileHandle | None = None
        self._log_basename = os.path.splitext(os.path.basename(sys.executable))[0] if is_frozen() else "kleinanzeigen_bot"
        self.log_file_path:str | None = abspath(f"{self._log_basename}.log")
        self._logfile_arg:str | None = None
        self._logfile_explicitly_provided:bool = False

        self.command = "help"
        self.ads_selector = "due"
        self._ads_selector_explicit:bool = False
        self.keep_old_ads = False

        # Ensure the attribute always exists on the bot object so that
        # capture_login_detection_diagnostics_if_enabled can read/write it
        # via getattr/setattr. The per-attempt reset happens in login_flow.login().
        self._login_detection_diagnostics_captured:bool = False
        self._timing_collector:"TimingCollector | None" = None

    def __del__(self) -> None:
        if self.file_log:
            self.file_log.close()
            self.file_log = None
        self.close_browser_session()

    def get_version(self) -> str:
        return __version__

    def _workspace_or_raise(self) -> _xdg_paths.Workspace:
        if self.workspace is None:
            raise AssertionError(_("Workspace must be resolved before command execution"))
        return self.workspace

    @property
    def _update_check_state_path(self) -> Path:
        return self._workspace_or_raise().state_dir / "update_check_state.json"

    async def run(self, args:list[str]) -> None:
        _cli = importlib.import_module("kleinanzeigen_bot.cli")
        parsed = _cli.parse_args(args)
        self.command = parsed.command
        self.ads_selector = parsed.ads_selector
        self._ads_selector_explicit = parsed.ads_selector_explicit
        self.keep_old_ads = parsed.keep_old_ads
        self._preserve_local_settings = parsed.preserve_local_settings
        self._config_arg = parsed.config_arg
        self._workspace_mode_arg = cast(_xdg_paths.InstallationMode, parsed.workspace_mode) if parsed.workspace_mode else None
        self._logfile_arg = parsed.logfile_arg
        self._logfile_explicitly_provided = parsed.logfile_explicitly_provided
        if parsed.config_file_path is not None:
            self.config_file_path = parsed.config_file_path
        if parsed.logfile_explicitly_provided:
            self.log_file_path = parsed.log_file_path

        self.workspace = _runtime_config.resolve_workspace(
            command = self.command,
            config_file_path = self.config_file_path,
            config_arg = self._config_arg,
            logfile_arg = self._logfile_arg,
            workspace_mode = self._workspace_mode_arg,
            logfile_explicitly_provided = self._logfile_explicitly_provided,
            log_basename = self._log_basename,
        )
        if self.workspace is not None:
            self.config_file_path = str(self.workspace.config_file)
            self.log_file_path = str(self.workspace.log_file) if self.workspace.log_file else None

        try:
            # When adding/removing a case, also update runtime_config.VALID_COMMANDS.
            match self.command:
                case "help":
                    _cli.show_help()
                    return
                case "version":
                    print(self.get_version())
                case "create-config":
                    self._handle_create_config()
                case "diagnose":
                    self._handle_diagnose()
                case "verify":
                    self._handle_verify()
                case "update-check":
                    self._handle_update_check()
                case "update-content-hash":
                    self._handle_update_content_hash()
                case "status":
                    self._handle_status()
                case "publish":
                    await self._handle_publish()
                case "update":
                    await self._handle_update()
                case "delete":
                    await self._handle_delete()
                case "extend":
                    await self._handle_extend()
                case "download":
                    await self._handle_download()
                case _:
                    LOG.error("Unknown command: %s", self.command)
                    sys.exit(2)
        finally:
            self.close_browser_session()
            if self._timing_collector is not None:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self._timing_collector.flush)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Timing collector flush failed: %s", exc)

    # ------------------------------------------------------------------
    # Bootstrap and shared helpers
    # ------------------------------------------------------------------

    def _bootstrap_runtime(self) -> None:
        self.file_log = _runtime_config.configure_file_logging(
            self.log_file_path,
            self.workspace,
            self.file_log,
            self.get_version(),
        )
        runtime_state = _runtime_config.load_config(self.config_file_path, self.workspace, self.command)
        self.config = runtime_state.config
        self.categories = runtime_state.categories
        self._timing_collector = runtime_state.timing_collector
        _runtime_config.apply_browser_config(self.browser_config, self.config, self.workspace, self.config_file_path)

    def _check_for_updates(self) -> None:
        """Run startup update check (browser session not needed)."""
        checker = _update_checker.UpdateChecker(self.config, self._update_check_state_path)
        checker.check_for_updates()

    async def _open_logged_in_browser(self) -> None:
        """Create a browser session and log in."""
        await self.create_browser_session()
        await self.login()

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _handle_create_config(self) -> None:
        if self.workspace is None and self._workspace_mode_arg is not None:
            try:
                workspace = _xdg_paths.resolve_workspace(
                    config_arg = self._config_arg,
                    logfile_arg = self._logfile_arg,
                    workspace_mode = self._workspace_mode_arg,
                    logfile_explicitly_provided = self._logfile_explicitly_provided,
                    log_basename = self._log_basename,
                )
                self.workspace = workspace
                self.config_file_path = str(workspace.config_file)
                self.log_file_path = str(workspace.log_file) if workspace.log_file else None
            except ValueError as exc:
                LOG.error(str(exc))
                sys.exit(2)
        _runtime_config.create_default_config(self.config_file_path, self.workspace)

    def _handle_diagnose(self) -> None:
        self._bootstrap_runtime()
        self.diagnose_browser_issues()

    def _handle_verify(self) -> None:
        self._bootstrap_runtime()
        self._check_for_updates()
        self.ads_selector = "all"
        self.load_ads(exclude_ads_with_id = False)
        LOG.info("############################################")
        LOG.info("DONE: No configuration errors found.")
        LOG.info("############################################")

    def _handle_update_check(self) -> None:
        # update-check uses sensible defaults and needs no config files,
        # no file logging, and no browser setup — skip bootstrap entirely.
        self.config = Config()
        checker = _update_checker.UpdateChecker(self.config, self._update_check_state_path)
        checker.check_for_updates(skip_interval_check = True)

    def _handle_update_content_hash(self) -> None:
        self._bootstrap_runtime()
        self._check_for_updates()
        self.ads_selector = "all"
        if ads := self.load_ads(exclude_ads_with_id = False):
            ad_loading.update_content_hashes(ads)
        else:
            LOG.info("############################################")
            LOG.info("DONE: No active ads found.")
            LOG.info("############################################")

    def _handle_status(self) -> None:
        """Show status overview of all local ads."""
        self._bootstrap_runtime()
        self._check_for_updates()

        loaded = ad_loading.load_ad_configs(
            config_file_path = self.config_file_path,
            ad_file_patterns = self.config.ad_files,
            ad_defaults = self.config.ad_defaults,
        )
        if not loaded:
            LOG.info("No ad files found.")
            return

        now = _misc.now()
        ads_for_status = [(relpath, ad_cfg, raw) for _abspath, relpath, ad_cfg, raw in loaded]
        rows = ad_status.build_status_rows(ads_for_status, now = now)
        use_color = _color.should_use_color()
        output = ad_status.render_status_rows(rows, color = use_color)
        print(output)

    async def _handle_publish(self) -> None:
        self._bootstrap_runtime()
        self._check_for_updates()

        if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all", "new", "due", "changed"}):
            if self._ads_selector_explicit:
                LOG.error(
                    'Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, new, due, changed) or numeric IDs.',
                    self.ads_selector,
                )
                sys.exit(2)
            self.ads_selector = "due"

        if ads := self.load_ads():
            await self._open_logged_in_browser()
            await self.publish_ads(ads)
        else:
            LOG.info("############################################")
            LOG.info("DONE: No new/outdated ads found.")
            LOG.info("############################################")

    async def _handle_update(self) -> None:
        self._bootstrap_runtime()
        # NOTE: intentionally no update check — see project safeguards

        if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all", "changed"}):
            if self._ads_selector_explicit:
                LOG.error('Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, changed) or numeric IDs.', self.ads_selector)
                sys.exit(2)
            self.ads_selector = "changed"

        if ads := self.load_ads():
            await self._open_logged_in_browser()
            await self.update_ads(ads)
        else:
            LOG.info("############################################")
            LOG.info("DONE: No changed ads found.")
            LOG.info("############################################")

    async def _handle_delete(self) -> None:
        self._bootstrap_runtime()
        self._check_for_updates()
        if ads := self.load_ads():
            await self._open_logged_in_browser()
            await delete_flow.delete_ads(
                web = self, root_url = self.root_url,
                after_delete = self.config.deleting.after_delete,
                delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title,
                ad_cfgs = ads,
            )
        else:
            LOG.info("############################################")
            LOG.info("DONE: No ads to delete found.")
            LOG.info("############################################")

    async def _handle_extend(self) -> None:
        self._bootstrap_runtime()
        self._check_for_updates()

        # Default to all ads if no selector provided, but reject invalid values
        if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all"}):
            if self._ads_selector_explicit:
                LOG.error('Invalid --ads selector: "%s". Valid values: all or comma-separated numeric IDs.', self.ads_selector)
                sys.exit(2)
            LOG.info("Extending all ads within 8-day window...")
            self.ads_selector = "all"

        if ads := self.load_ads():
            await self._open_logged_in_browser()
            await extend_flow.extend_ads(
                web = self, root_url = self.root_url,
                ad_cfgs = ads,
            )
        else:
            LOG.info("############################################")
            LOG.info("DONE: No ads found to extend.")
            LOG.info("############################################")

    async def _handle_download(self) -> None:
        # ad IDs depends on selector — validate before bootstrap/config loading
        if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all", "new"}):
            if self._ads_selector_explicit:
                LOG.error('Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, new) or numeric IDs.', self.ads_selector)
                sys.exit(2)
            self.ads_selector = "new"
        self._bootstrap_runtime()
        if self._preserve_local_settings:
            self.config.download.preserve_local_settings = True
        self._check_for_updates()
        await self._open_logged_in_browser()
        await download_flow.download_ads(
            web = self, config = self.config,
            config_file_path = self.config_file_path,
            workspace = self._workspace_or_raise(),
            ads_selector = self.ads_selector,
            load_ads_func = self.load_ads,
            root_url = self.root_url,
        )

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        """Load and validate all ad config files.

        Delegates to :func:`ad_loading.load_ads` with the current config,
        selector, and category context for filtering and validation.
        """
        return ad_loading.load_ads(
            config_file_path = self.config_file_path,
            ad_file_patterns = self.config.ad_files,
            ad_defaults = self.config.ad_defaults,
            categories = self.categories,
            ads_selector = self.ads_selector,
            command = self.command,
            ignore_inactive = ignore_inactive,
            exclude_ads_with_id = exclude_ads_with_id,
        )

    # ------------------------------------------------------------------
    # Login / auth flow — thin delegators to login_flow module
    # ------------------------------------------------------------------

    async def login(self) -> None:
        await _login_flow.login(
            self,
            username = self.config.login.username,
            password = self.config.login.password,
            captcha_config = self.config.captcha,
            root_url = self.root_url,
            log_file_path = self.log_file_path,
            diagnostics_config = getattr(self.config, "diagnostics", None),
            diagnostics_output_dir_fn = self._diagnostics_output_dir,
        )

    async def get_login_state(self, *, capture_diagnostics:bool = True) -> LoginDetectionResult:
        return await _login_flow.get_login_state(
            self,
            username = self.config.login.username,
            capture_diagnostics = capture_diagnostics,
            diagnostics_config = getattr(self.config, "diagnostics", None),
            diagnostics_output_dir_fn = self._diagnostics_output_dir,
            log_file_path = self.log_file_path,
        )

    def _diagnostics_output_dir(self) -> Path:
        diagnostics = getattr(self.config, "diagnostics", None)
        if diagnostics is not None and diagnostics.output_dir and diagnostics.output_dir.strip():
            return Path(abspath(diagnostics.output_dir, relative_to = self.config_file_path)).resolve()

        workspace = self._workspace_or_raise()
        _xdg_paths.ensure_directory(workspace.diagnostics_dir, "diagnostics directory")
        return workspace.diagnostics_dir

    async def _capture_publish_error_diagnostics_if_enabled(
        self,
        ad_cfg:Ad,
        ad_cfg_orig:dict[str, Any],
        ad_file:str,
        attempt:int,
        exc:Exception,
    ) -> None:
        """Capture publish failure diagnostics when enabled and a page is available.

        Runs only if cfg.capture_on.publish is enabled and self.page is set.
        Uses the ad configuration and publish attempt details to write screenshot, HTML,
        JSON payload, and optional log copy for debugging.
        """
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.publish:
            return

        page = getattr(self, "page", None)
        if page is None:
            return

        # Use the ad filename (without extension) as identifier
        ad_file_stem = Path(ad_file).stem

        json_payload = {
            "timestamp": _misc.now().isoformat(timespec = "seconds"),
            "attempt": attempt,
            "page_url": getattr(page, "url", None),
            "exception": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "repr": repr(exc),
            },
            "ad_file": ad_file,
            "ad_title": ad_cfg.title,
            "ad_config_effective": ad_cfg.model_dump(mode = "json"),
            "ad_config_original": ad_cfg_orig,
        }

        try:
            await _diagnostics.capture_diagnostics(
                output_dir = self._diagnostics_output_dir(),
                base_prefix = "publish_error",
                attempt = attempt,
                subject = ad_file_stem,
                page = page,
                json_payload = json_payload,
                log_file_path = self.log_file_path,
                copy_log = cfg.capture_log_copy,
            )
        except Exception as error:  # noqa: BLE001
            LOG.warning("Diagnostics capture failed during publish error handling: %s", error)

    async def is_logged_in(self) -> bool:
        return await _login_flow.is_logged_in(self, username = self.config.login.username)

    async def publish_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        await _publishing_workflow.publish_ads(
            self, ad_cfgs,
            root_url = self.root_url,
            config = self.config,
            keep_old_ads = self.keep_old_ads,
            capture_diagnostics = self._capture_publish_error_diagnostics_if_enabled,
            config_file_path = self.config_file_path,
        )

    async def publish_ad(
        self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], published_ads_list:list[PublishedAd], mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE
    ) -> None:
        """Publish or update an ad on Kleinanzeigen.

        Args:
            ad_file: Path to the ad configuration YAML file.
            ad_cfg: The effective ad configuration with default values applied.
            ad_cfg_orig: The original ad configuration as present in the YAML file.
            published_ads_list: List of published ads from the API, used for deduplication
                and old ad deletion.
            mode: The ad editing strategy. REPLACE creates a new ad (full republish),
                MODIFY updates an existing ad in-place.

        Returns:
            None
        """
        await _publishing_workflow.publish_ad(
            self, ad_file, ad_cfg, ad_cfg_orig, published_ads_list, mode,
            root_url = self.root_url,
            config = self.config,
            keep_old_ads = self.keep_old_ads,
            config_file_path = self.config_file_path,
        )

    async def update_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        """
        Updates a list of ads.
        The list gets filtered, so that only already published ads will be updated.
        Calls publish_ad in MODIFY mode.

        Args:
            ad_cfgs: List of ad configurations

        Returns:
            None
        """
        await _publishing_workflow.update_ads(
            self, ad_cfgs,
            root_url = self.root_url,
            config = self.config,
            keep_old_ads = self.keep_old_ads,
            capture_diagnostics = self._capture_publish_error_diagnostics_if_enabled,
            config_file_path = self.config_file_path,
        )
