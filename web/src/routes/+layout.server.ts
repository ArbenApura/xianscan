import type { LayoutServerLoad } from './$types';
import {
	THEME_COOKIE,
	LIB_LAYOUT_COOKIE,
	CH_LAYOUT_COOKIE,
	READER_VIEW_COOKIE,
	WEBTOON_KIND_COOKIE,
	WEBTOON_WIDTH_COOKIE,
	type Theme,
} from '$lib/stores/settings';

export interface UserPreferences {
	theme: Theme;
	libraryLayout: 'grid' | 'list' | 'compact';
	chapterLayout: 'grid' | 'list' | 'compact';
	readerViewMode: 'reader' | 'grid' | 'compare';
	webtoonKind: 'output' | 'original';
	webtoonWidth: 'sm' | 'md' | 'lg';
}

const VALID_THEMES = new Set<Theme>(['light', 'sepia', 'dark']);
const VALID_LAYOUTS = new Set(['grid', 'list', 'compact']);
const VALID_READER_MODES = new Set(['reader', 'grid', 'compare']);
const VALID_WEBTOON_KINDS = new Set(['output', 'original']);
const VALID_WEBTOON_WIDTHS = new Set(['sm', 'md', 'lg']);

export const load: LayoutServerLoad = async ({ cookies }) => {
	const rawTheme = cookies.get(THEME_COOKIE);
	const theme: Theme = VALID_THEMES.has(rawTheme as Theme) ? (rawTheme as Theme) : 'sepia';

	const rawLib = cookies.get(LIB_LAYOUT_COOKIE);
	const libraryLayout = VALID_LAYOUTS.has(rawLib as any) ? (rawLib as 'grid' | 'list' | 'compact') : 'grid';

	const rawCh = cookies.get(CH_LAYOUT_COOKIE);
	const chapterLayout = VALID_LAYOUTS.has(rawCh as any) ? (rawCh as 'grid' | 'list' | 'compact') : 'grid';

	const rawReader = cookies.get(READER_VIEW_COOKIE);
	const readerViewMode = VALID_READER_MODES.has(rawReader as any)
		? (rawReader as 'reader' | 'grid' | 'compare')
		: 'reader';

	const rawKind = cookies.get(WEBTOON_KIND_COOKIE);
	const webtoonKind = VALID_WEBTOON_KINDS.has(rawKind as any) ? (rawKind as 'output' | 'original') : 'output';

	const rawWidth = cookies.get(WEBTOON_WIDTH_COOKIE);
	const webtoonWidth = VALID_WEBTOON_WIDTHS.has(rawWidth as any) ? (rawWidth as 'sm' | 'md' | 'lg') : 'md';

	const preferences: UserPreferences = {
		theme,
		libraryLayout,
		chapterLayout,
		readerViewMode,
		webtoonKind,
		webtoonWidth,
	};

	return {
		preferences,
	};
};
