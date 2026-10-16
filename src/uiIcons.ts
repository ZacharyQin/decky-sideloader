// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- provide distinct themed update glyphs and decorative action SVGs without adding controls.
// - participant: docs/upstream-conflict/sideload/diagnostics-sharing.md -- provide decorative pickup, copy, details and stop glyphs.
// - participant: docs/upstream-conflict/sideload/diagnostics.md -- provide the decorative export action glyph.
import {
  FaArrowUp, FaArrowLeft, FaArchive, FaCheckCircle, FaCog, FaDownload, FaExchangeAlt,
  FaExclamationTriangle, FaQuestionCircle, FaFileAlt, FaGithub, FaHistory, FaLink, FaList, FaPuzzlePiece, FaSearch,
  FaShieldAlt, FaSyncAlt, FaTimesCircle, FaTrashAlt, FaUnlink, FaQrcode, FaCopy,
} from "react-icons/fa";
import { updaterFieldClasses } from "@decky/ui";
import type { IconType } from "react-icons";

export const icons = {
  back: FaArrowLeft, checkUpdates: FaSyncAlt, newInstall: FaDownload,
  settings: FaCog, binding: FaLink, manageBinding: FaGithub,
  saveBinding: FaCheckCircle, unlink: FaUnlink, replace: FaExchangeAlt,
  reinstall: FaSyncAlt, uninstall: FaTrashAlt, search: FaSearch, searchMode: FaSearch, manualRepo: FaGithub,
  returnSearch: FaArrowLeft, probeRepo: FaShieldAlt, readReleases: FaHistory,
  installPackage: FaDownload, changePlugin: FaPuzzlePiece, changeRepo: FaGithub,
  releaseNotes: FaFileAlt, reloadSettings: FaSyncAlt,
  dismissTask: FaTimesCircle, viewTask: FaList, refreshTask: FaSyncAlt, confirmInstall: FaShieldAlt,
  shareDiagnostics: FaQrcode, copyDiagnostics: FaCopy, stopDiagnostics: FaTimesCircle, diagnosticDetails: FaFileAlt,
  exportDiagnostics: FaFileAlt, takeover: FaShieldAlt, returnStore: FaExchangeAlt, readOwnership: FaSyncAlt,
  cancelRequest: FaTimesCircle, finishTracking: FaArchive,
} satisfies Record<string, IconType>;
export type IconKey = keyof typeof icons;

export const updateIcon = FaArrowUp;
export const updateClassName = updaterFieldClasses.UpdaterFieldStatusSuccess;

export const checkStatusIcons = {
  update: FaArrowUp, error: FaExclamationTriangle, current: FaCheckCircle, ahead: FaCheckCircle,
  uncomparable: FaQuestionCircle, no_release: FaQuestionCircle, no_asset: FaQuestionCircle, unbound: FaLink,
};
export const historicalIcon = FaHistory;
