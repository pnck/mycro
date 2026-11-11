// API and auth helpers for communication with CircuitPython device
import { configStore } from "../store";

export function getToken(): string {
	const tokenEl = document.getElementById("token") as HTMLInputElement | null;
	const domVal = tokenEl ? tokenEl.value.trim() : "";
	if (domVal) return domVal;
	return configStore.get().token;
}

export function headers(contentType: string = "application/x-www-form-urlencoded"): Record<string, string> {
	const h: Record<string, string> = { "Content-Type": contentType };
	const t = getToken();
	if (t) {
		h["Authorization"] = "Bearer " + t;
	}
	return h;
}
