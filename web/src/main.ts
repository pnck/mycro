import "./styles/main.css";
import "./styles/pixel-assist.css";
import "./modules/manual/manual.css";
import { initMacroEditor } from "./modules/editor";
import { initMacroController } from "./modules/macro-controller";
import { initMacroPresets } from "./modules/macro-presets";
import { initAutoClicker } from "./modules/auto-clicker";
import { initPixelAssist } from "./modules/pixel-assist";
import { initManualDrawer } from "./modules/manual";

function initApp(): void {
	initMacroEditor();
	initMacroController();
	initMacroPresets();
	initAutoClicker();
	initPixelAssist();
	initManualDrawer();
}

initApp();
