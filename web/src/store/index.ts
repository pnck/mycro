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

export interface PersistentStore<T> {
	get(): T;
	set(val: T): void;
}

export interface PersistentMapStore<T> extends PersistentStore<T> {
	setKey<K extends keyof T>(key: K, val: T[K]): void;
}

/**
 * Creates a persistent store backed by localStorage with safe fallback.
 */
export function persistentAtom<T>(key: string, initialValue: T): PersistentStore<T> {
	let value = initialValue;
	try {
		const raw = localStorage.getItem(key);
		if (raw !== null) value = JSON.parse(raw);
	} catch {}

	return {
		get: () => value,
		set: (newVal: T) => {
			value = newVal;
			try {
				localStorage.setItem(key, JSON.stringify(newVal));
			} catch {}
		}
	};
}

/**
 * Creates a persistent map store backed by localStorage with shallow merge.
 */
export function persistentMap<T extends Record<string, any>>(key: string, initialValue: T): PersistentMapStore<T> {
	let value = initialValue;
	try {
		const raw = localStorage.getItem(key);
		if (raw !== null) {
			const parsed = JSON.parse(raw);
			if (typeof parsed === "object" && parsed !== null) {
				value = { ...initialValue, ...parsed };
			}
		}
	} catch {}

	return {
		get: () => value,
		set: (newVal: T) => {
			value = newVal;
			try {
				localStorage.setItem(key, JSON.stringify(newVal));
			} catch {}
		},
		setKey: <K extends keyof T>(k: K, val: T[K]) => {
			value = { ...value, [k]: val };
			try {
				localStorage.setItem(key, JSON.stringify(value));
			} catch {}
		}
	};
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

export const activePresetStore = persistentAtom<string>("mycro_active_preset", "");
export const workspaceStore = persistentAtom<string>("mycro_workspace_code", "");
