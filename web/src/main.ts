import "./styles/main.css";
import "./styles/pixel-assist.css";
import { initMacroController } from "./modules/macro-controller";
import { initAutoClicker } from "./modules/auto-clicker";
import { initPixelAssist } from "./modules/pixel-assist";

function initApp(): void {
	initMacroController();
	initAutoClicker();
	initPixelAssist();
}

initApp();
