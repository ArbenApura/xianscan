import { error } from '@sveltejs/kit';
import { getChapterReaderData } from '$lib/server/chapters';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const chapterId = Number(params.chapterId);
	if (!Number.isInteger(chapterId)) {
		throw error(400, 'Invalid chapter id.');
	}

	const data = await getChapterReaderData(chapterId);
	if (data.chapter.bookId !== params.id) {
		throw error(404, 'Chapter does not belong to the specified book.');
	}

	return {
		chapter: data.chapter,
		prevChapter: data.prevChapter,
		nextChapter: data.nextChapter,
		allChapters: data.allChapters,
		pages: data.pages,
	};
};
