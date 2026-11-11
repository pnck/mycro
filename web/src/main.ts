import "./styles/main.css";
import "./styles/pixel-assist.css";
import "./styles/manual-drawer.css";
import { initMacroEditor } from "./modules/editor";
import { initMacroController } from "./modules/macro-controller";
import { initMacroPresets } from "./modules/macro-presets";
import { initAutoClicker } from "./modules/auto-clicker";
import { initPixelAssist } from "./modules/pixel-assist";
import { initManualDrawer } from "./modules/manual-drawer";

function initApp(): void {
	initMacroEditor();
	initMacroController();
	initMacroPresets();
	initAutoClicker();
	initPixelAssist();
	initManualDrawer();
}

initApp();
