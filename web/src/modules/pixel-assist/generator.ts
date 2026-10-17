/**
 * Pure Macro DSL v3 code generator.
 * Zero DOM dependencies; strictly transforms coordinate & pixel data into DSL strings.
 */

export function toHex(r: number, g: number, b: number): string {
	return ((r << 16) | (g << 8) | b).toString(16).toUpperCase().padStart(6, "0");
}

export function generateRectLiteral(
	getPixel: (x: number, y: number) => [number, number, number],
	rx: number,
	ry: number,
	rw: number,
	rh: number
): string {
	const rows: string[] = [];
	for (let j = 0; j < rh; j++) {
		const cells: string[] = [];
		for (let i = 0; i < rw; i++) {
			const [r, g, b] = getPixel(rx + i, ry + j);
			cells.push("0x" + toHex(r, g, b));
		}
		rows.push("\\row{" + cells.join(",") + "}");
	}
	return "\\rect{" + rows.join(",\n  ") + "}";
}

export function generatePointSnippet(x: number, y: number, hex: string): string {
	return [
		"\\use{img}\\",
		`\\call{img.snap}{${x},${y},1,1}\\set{px}{$ret}\\`,
		"\\call{img.at}{$px}{0,0}\\",
		`\\ifnum{$ret}{=}{0x${hex}}{\\# color matched`,
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
	const rectArg = `${rx},${ry},${rw},${rh}`;
	if (rw <= 16 && rh <= 16) {
		const refLiteral = generateRectLiteral(getPixel, rx, ry, rw, rh);
		return [
			"\\use{img}\\",
			`\\set{ref}{${refLiteral}}\\`,
			`\\call{img.snap}{${rectArg}}\\set{cur}{$ret}\\`,
			"\\call{img.diff}{$ref}{$cur}{16}\\",
			"\\ifnum{$ret}{=}{0}{\\# identical",
			"}{\\# changed",
			"}"
		].join("\n");
	}

	return [
		"\\use{img}\\",
		`\\call{img.snap}{${rectArg}}\\set{ref}{$ret}\\`,
		`\\call{img.snap}{${rectArg}}\\set{cur}{$ret}\\`,
		"\\call{img.diff}{$ref}{$cur}{16}\\",
		"\\ifnum{$ret}{=}{0}{\\# identical",
		"}{\\# changed",
		"}"
	].join("\n");
}
