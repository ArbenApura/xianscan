// WATERMARK DETECTION & FILTERING ENGINE.
// IDENTIFIES SCANLATION WATERMARKS, SITE URLS, AND STAMPS TO:
//   1) INCLUDE THEM IN LA-MA CLEANING (TO ERASE THEM FROM THE IMAGE)
//   2) EXCLUDE THEM FROM LLM TRANSLATION & TYPESETTING (SAVING TOKENS & PREVENTING GARBAGE OUTPUT)

import type { PipelineRegion } from './pipeline-client';

export const DEFAULT_WATERMARK_PATTERNS: string[] = [
	'baozimh',
	'包子',
	'kuaikan',
	'快看',
	'bilibili',
	'哔哩哔哩',
	'嗶哩嗶哩',
	'腾讯',
	'騰訊',
	'汉化',
	'漢化',
	'微信',
	'公众号',
	'公眾號',
	'qq群',
	'qq:',
	'qq：',
	'严禁转载',
	'嚴禁轉載',
	'独家',
	'獨家',
	'mangabox',
	'comick',
];

const URL_REGEX = /(https?:\/\/|www\.|[a-zA-Z0-9-]+\.(com|net|org|cc|xyz|me|site|info|tw|cn|io|app|club|fun|top))(?:\/[^\s]*)?/i;

/**
 * RETURNS true IF THE OCR TEXT MATCHES KNOWN WATERMARK REGEXES, SITE URLS, OR CUSTOM PATTERNS.
 */
export function isWatermarkText(text: string, customPatterns: string[] = []): boolean {
	const trimmed = text.trim();
	if (!trimmed) return false;

	// 1. CHECK URL / DOMAIN PATTERNS
	if (URL_REGEX.test(trimmed)) return true;

	const lower = trimmed.toLowerCase();

	// 2. CHECK DEFAULT WATERMARK KEYWORDS
	for (const pattern of DEFAULT_WATERMARK_PATTERNS) {
		if (lower.includes(pattern.toLowerCase())) return true;
	}

	// 3. CHECK USER CUSTOM PATTERNS
	for (const custom of customPatterns) {
		const c = custom.trim().toLowerCase();
		if (c && lower.includes(c)) return true;
	}

	return false;
}

export interface PartitionedRegions {
	textRegions: PipelineRegion[];
	watermarkRegions: PipelineRegion[];
}

/**
 * PARTITIONS PIPELINE REGIONS INTO TEXT REGIONS (TO TRANSLATE) AND WATERMARK REGIONS (TO ERASE ONLY).
 * IF A MULTI-LINE REGION CONTAINS A MIX OF WATERMARK LINES AND NON-WATERMARK TEXT LINES,
 * IT IS SPLIT ALONG LINE BOUNDARIES SO LEGITIMATE TEXT IS PRESERVED FOR TRANSLATION AND TYPESETTING,
 * WHILE WATERMARK LINES ARE SEPARATED FOR ERASE-ONLY INPAINTING.
 */
export function filterWatermarkRegions(
	regions: PipelineRegion[],
	customPatterns: string[] = [],
): PartitionedRegions {
	const textRegions: PipelineRegion[] = [];
	const watermarkRegions: PipelineRegion[] = [];

	for (const region of regions) {
		const rawLines = region.text.split('\n');
		if (rawLines.length <= 1) {
			if (isWatermarkText(region.text, customPatterns)) {
				watermarkRegions.push({
					...region,
					category: 'other', // MARKED AS OTHER / WATERMARK
				});
			} else {
				textRegions.push(region);
			}
			continue;
		}

		// MULTI-LINE REGION: CHECK LINE BY LINE
		const lineInfos = rawLines.map((lineText) => ({
			text: lineText,
			isWatermark: isWatermarkText(lineText, customPatterns),
		}));

		const hasWatermark = lineInfos.some((l) => l.isWatermark);
		const hasNonWatermark = lineInfos.some((l) => !l.isWatermark);

		if (!hasWatermark) {
			// ALL LINES ARE VALID TEXT
			textRegions.push(region);
			continue;
		}

		if (!hasNonWatermark) {
			// ALL LINES ARE WATERMARKS
			watermarkRegions.push({
				...region,
				category: 'other',
			});
			continue;
		}

		// MIXED REGION: SPLIT ALONG LINE BOUNDARIES INTO SUB-REGIONS
		const lineH = region.box.h / rawLines.length;
		const groups: { text: string[]; isWatermark: boolean; startIdx: number; count: number }[] = [];
		let currentGroup: { text: string[]; isWatermark: boolean; startIdx: number; count: number } | null = null;

		for (let i = 0; i < lineInfos.length; i++) {
			const info = lineInfos[i];
			if (!currentGroup || currentGroup.isWatermark !== info.isWatermark) {
				currentGroup = { text: [info.text], isWatermark: info.isWatermark, startIdx: i, count: 1 };
				groups.push(currentGroup);
			} else {
				currentGroup.text.push(info.text);
				currentGroup.count++;
			}
		}

		for (let gIdx = 0; gIdx < groups.length; gIdx++) {
			const g = groups[gIdx];
			const subY = Math.round(region.box.y + g.startIdx * lineH);
			const subH = Math.max(1, Math.round(g.count * lineH));
			const subBox = { x: region.box.x, y: subY, w: region.box.w, h: subH };
			const subPolygon = [
				[subBox.x, subBox.y],
				[subBox.x + subBox.w, subBox.y],
				[subBox.x + subBox.w, subBox.y + subBox.h],
				[subBox.x, subBox.y + subBox.h],
			];
			const subRegion: PipelineRegion = {
				...region,
				id: `${region.id}_s${gIdx}`,
				box: subBox,
				polygon: subPolygon,
				text: g.text.join('\n'),
				category: g.isWatermark ? 'other' : region.category,
			};

			if (g.isWatermark) {
				watermarkRegions.push(subRegion);
			} else {
				textRegions.push(subRegion);
			}
		}
	}

	return { textRegions, watermarkRegions };
}
