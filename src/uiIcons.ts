import {
  FaArrowUp, FaArrowLeft, FaArchive, FaCheckCircle, FaCog, FaDownload, FaExchangeAlt,
  FaExclamationTriangle, FaQuestionCircle, FaFileAlt, FaGithub, FaHistory, FaLink, FaList, FaPuzzlePiece, FaRedoAlt, FaSearch,
  FaShieldAlt, FaSyncAlt, FaTimesCircle, FaTrashAlt, FaUnlink, FaQrcode, FaCopy, FaTools, FaGlobe,
} from "react-icons/fa";
import { updaterFieldClasses } from "@decky/ui";
import type { IconType } from "react-icons";

export const icons = {
  back: FaArrowLeft, checkUpdates: FaSyncAlt, newInstall: FaDownload,
  settings: FaCog, binding: FaLink, manageBinding: FaGithub,
  saveBinding: FaCheckCircle, unlink: FaUnlink, replace: FaExchangeAlt,
  reinstall: FaSyncAlt, uninstall: FaTrashAlt, search: FaSearch, manualRepo: FaGithub,
  returnSearch: FaArrowLeft, readReleases: FaHistory, refreshReleases: FaSyncAlt,
  installPackage: FaDownload, changePlugin: FaPuzzlePiece, changeRepo: FaGithub,
  releaseNotes: FaFileAlt, licenses: FaFileAlt, reloadSettings: FaSyncAlt,
  dismissTask: FaTimesCircle, viewTask: FaList, refreshTask: FaSyncAlt, refreshRecognition: FaRedoAlt, deletePackage: FaTrashAlt, confirmInstall: FaShieldAlt,
  shareDiagnostics: FaQrcode, copyDiagnostics: FaCopy, stopDiagnostics: FaTimesCircle, diagnosticDetails: FaFileAlt,
  exportDiagnostics: FaFileAlt, takeover: FaShieldAlt, returnStore: FaExchangeAlt, readOwnership: FaSyncAlt,
  cancelRequest: FaTimesCircle, finishTracking: FaArchive, skipRelease: FaTimesCircle, continueInstall: FaShieldAlt, retryDownload: FaSyncAlt,
  maintenance: FaTools, network: FaGlobe,
  deleteBackups: FaTrashAlt,
  updateRepositoryMap: FaSyncAlt,
} satisfies Record<string, IconType>;
export type IconKey = keyof typeof icons;

export const updateIcon = FaArrowUp;
export const updateClassName = updaterFieldClasses.UpdaterFieldStatusSuccess;

export const checkStatusIcons = {
  update: FaArrowUp, error: FaExclamationTriangle, current: FaCheckCircle, ahead: FaCheckCircle,
  uncomparable: FaQuestionCircle, no_release: FaQuestionCircle, no_asset: FaQuestionCircle, unbound: FaLink,
};
export const historicalIcon = FaHistory;
