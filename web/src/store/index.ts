import { atom, map, onSet } from "nanostores";

export interface StoredMacro {
	name: string;
	code: string;
	updatedAt?: number;
}

export interface DrawerState {
	isOpen: boolean;
	collapsed: string[];
}

export interface ConfigState {
	token: string;
	holdMs: number;
}

/**
 * Creates a persistent nanostore Atom backed by localStorage with error handling.
 */
export function persistentAtom<T>(key: string, initialValue: T) {
	let initial = initialValue;
	try {
		const raw = localStorage.getItem(key);
		if (raw !== null) {
			const parsed = JSON.parse(raw);
			initial = parsed;
		}
	} catch {
		// Fallback to default
	}

	const store = atom<T>(initial);

	onSet(store, ({ newValue }) => {
		try {
			localStorage.setItem(key, JSON.stringify(newValue));
		} catch {
			// Storage quota / security policy fail-safe
		}
	});

	return store;
}

/**
 * Creates a persistent nanostore Map backed by localStorage with shallow merge and error handling.
 */
export function persistentMap<T extends Record<string, any>>(key: string, initialValue: T) {
	let initial = initialValue;
	try {
		const raw = localStorage.getItem(key);
		if (raw !== null) {
			const parsed = JSON.parse(raw);
			if (typeof parsed === "object" && parsed !== null) {
				initial = { ...initialValue, ...parsed };
			}
		}
	} catch {
		// Fallback to default
	}

	const store = map<T>(initial);

	onSet(store, ({ newValue }) => {
		try {
			localStorage.setItem(key, JSON.stringify(newValue));
		} catch {
			// Storage quota / security policy fail-safe
		}
	});

	return store;
}

// Global persistent stores
export const drawerStore = persistentMap<DrawerState>("mycro_drawer", {
	isOpen: false,
	collapsed: []
});

export const presetsStore = persistentAtom<StoredMacro[]>("mycro_user_macros", []);

export const configStore = persistentMap<ConfigState>("mycro_config", {
	token: "",
	holdMs: 10
});

export const activePresetStore = atom<string>("");

