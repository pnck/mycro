/**
 * Pure Macro DSL v4 code generator.
 * Zero DOM dependencies; strictly transforms coordinate & pixel data into DSL strings.
 */

export function toHex(r: number, g: number, b: number): string {
	return ((r << 16) | (g << 8) | b).toString(16).toUpperCase().padStart(6, "0");
}

export function generatePointSnippet(x: number, y: number, hex: string): string {
	return [
		"\\use{img}\\",
		`\\call{img.snap}{${x}, ${y}, 1, 1}\\set{snap}{$ret}\\`,
		"\\call{img.at}{$snap}{0, 0}\\",
		`\\if{ {$ret, =, 0x${hex}} }{\\# color matched`,
		"}{\\# color mismatched",
		"}"
	].join("\n");
}

export function generateRegionSnippet(
	rx: number,
	ry: number,
	rw: number,
	rh: number,
	getPixel: (x: number, y: number) => [number, number, number]
): string {
	const rectArg = `${rx}, ${ry}, ${rw}, ${rh}`;

	// For regions up to 1024 pixels (e.g. 32x32, 64x16), generate exact pixelrect reference matrix
	if (rw * rh <= 1024 && rw <= 128 && rh <= 128) {
		const rows: string[] = [];
		for (let j = 0; j < rh; j++) {
			const cells: string[] = [];
			for (let i = 0; i < rw; i++) {
				const [r, g, b] = getPixel(rx + i, ry + j);
				cells.push("0x" + toHex(r, g, b));
			}
			rows.push("\\row{" + cells.join(", ") + "}");
		}
		const rowsStr = rows.join(",\n  ");
		return [
			"\\use{img}\\",
			`\\pixelrect{ref}{\n  ${rowsStr}\n}\\`,
			`\\call{img.snap}{${rectArg}}\\set{cur}{$ret}\\`,
			"\\call{img.diff}{$ref}{$cur}{16}\\",
			"\\if{ {$ret, =, 0} }{\\# region matched",
			"}{\\# region changed",
			"}"
		].join("\n");
	}

	// For larger regions (>1024 pixels), generate a dynamic screen change detection template
	return [
		"\\use{img}\\",
		`\\call{img.snap}{${rectArg}}\\set{ref}{$ret}\\`,
		"\\# Wait for screen update or trigger action:\\",
		"\\delay{200ms}\\",
		`\\call{img.snap}{${rectArg}}\\set{cur}{$ret}\\`,
		"\\call{img.diff}{$ref}{$cur}{16}\\",
		"\\if{ {$ret, =, 0} }{\\# region unchanged",
		"}{\\# region changed",
		"}"
	].join("\n");
}
