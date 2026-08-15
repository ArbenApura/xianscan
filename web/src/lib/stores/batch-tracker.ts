// GLOBAL BATCH TRANSLATION TRACKER STORE
// Orchestrates sequential chapter execution with mandatory pre-translation smart re-slicing,
// staged parallel page processing, SSE streaming, localStorage persistence, and self-healing error recovery.

import { writable, derived, get } from 'svelte/store';
import { browser } from '$app/environment';
import { toast } from 'svelte-sonner';
import { streamSse } from '$lib/sse';
import { jobTracker } from './job-tracker';
import { settings } from './settings';
import type { BatchChapterItem, BatchTranslationState, ChapterJobSnapshot } from '$lib/types';

const STORAGE_KEY = 'xianscan:batch_translation';

const initialBatchState: BatchTranslationState = {
	active: false,
	status: 'idle',
	bookId: null,
	bookTitle: null,
	queue: [],
	currentIndex: 0,
	currentPhase: undefined,
	force: false,
	startedAt: null,
	completedAt: null,
	totalCostUsd: 0,
	totalPromptTokens: 0,
	totalCompletionTokens: 0,
};

function loadStoredState(): BatchTranslationState {
	if (!browser) return initialBatchState;
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return initialBatchState;
		const parsed = JSON.parse(raw);
		if (parsed && Array.isArray(parsed.queue) && parsed.queue.length > 0) {
			return {
				...initialBatchState,
				...parsed,
			};
		}
	} catch {
		// Ignore corrupted state
	}
	return initialBatchState;
}

function saveState(state: BatchTranslationState): void {
	if (!browser) return;
	try {
		if (!state.active && state.status === 'idle') {
			localStorage.removeItem(STORAGE_KEY);
		} else {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
		}
	} catch {
		// Ignore storage quota errors
	}
}

function createBatchTrackerStore() {
	const { subscribe, set, update } = writable<BatchTranslationState>(loadStoredState());

	let isProcessingQueue = false;
	let unsubscribeJobTracker: (() => void) | null = null;
	let lastWatchedChapterId: number | null = null;
	let lastWatchedStatus: string | null = null;
	let currentResliceController: AbortController | null = null;

	// Helper to run smart reslice via SSE stream
	async function resliceChapter(
		chapterId: number,
		onProgress?: (message: string) => void,
		signal?: AbortSignal,
	): Promise<{ originalCount: number; newCount: number } | null> {
		if (!browser) return null;
		let result: { originalCount: number; newCount: number } | null = null;

		try {
			await streamSse(
				`/api/chapters/${chapterId}/reslice`,
				{},
				(e) => {
					if (e.type === 'progress' && typeof e.message === 'string') {
						onProgress?.(e.message);
					} else if (e.type === 'done') {
						result = {
							originalCount: (e.originalCount as number) || 0,
							newCount: (e.newCount as number) || 0,
						};
					} else if (e.type === 'error') {
						console.warn(`Reslice notice for chapter ${chapterId}:`, e.message);
					}
				},
				signal,
			);
			return result;
		} catch (err: any) {
			if (signal?.aborted) return null;
			console.warn(`Reslice stream issue for chapter ${chapterId}:`, err);
			return null;
		}
	}

	let livenessTimer: ReturnType<typeof setInterval> | null = null;

	function startLivenessWatchdog() {
		if (livenessTimer || !browser) return;
		livenessTimer = setInterval(async () => {
			const currentState = get({ subscribe });
			if (!currentState.active || currentState.status !== 'running') {
				stopLivenessWatchdog();
				return;
			}
			const currentChapter = currentState.queue[currentState.currentIndex];
			if (currentChapter && currentChapter.status === 'processing') {
				const jtState = get(jobTracker);
				const job = jtState.jobs[currentChapter.id];
				// If not connected or connection lost while supposed to be processing, auto-sync from server
				if (!job || job.connectionState === 'idle' || !job.running) {
					await jobTracker.syncChapter(currentChapter.id);
				}
			}
		}, 2500);
	}

	function stopLivenessWatchdog() {
		if (livenessTimer) {
			clearInterval(livenessTimer);
			livenessTimer = null;
		}
	}

	function detachJobWatcher() {
		stopLivenessWatchdog();
		if (unsubscribeJobTracker) {
			unsubscribeJobTracker();
			unsubscribeJobTracker = null;
		}
		lastWatchedChapterId = null;
		lastWatchedStatus = null;
	}

	// Watch jobTracker to advance the queue automatically
	function attachJobWatcher() {
		startLivenessWatchdog();
		if (unsubscribeJobTracker) return;

		unsubscribeJobTracker = jobTracker.subscribe((trackerState) => {
			const currentState = get({ subscribe });
			if (!currentState.active || currentState.status !== 'running') return;

			const currentChapter = currentState.queue[currentState.currentIndex];
			if (!currentChapter) {
				finishBatch();
				return;
			}

			// Only watch if in 'processing' translation stage
			if (currentChapter.status !== 'processing') return;

			const jobState = trackerState.jobs[currentChapter.id];
			if (!jobState) return;

			// Update live progress in queue item
			if (jobState.snapshot) {
				const snap = jobState.snapshot;
				update((s) => {
					const q = [...s.queue];
					if (q[s.currentIndex] && q[s.currentIndex].id === currentChapter.id) {
						q[s.currentIndex] = {
							...q[s.currentIndex],
							translatedPages: snap.completedPages,
							totalPages: snap.totalPages || snap.pages.length,
						};
					}
					const next = { ...s, queue: q };
					saveState(next);
					return next;
				});
			}

			const isDone =
				jobState.snapshot?.status === 'done' ||
				(!jobState.running &&
					jobState.snapshot?.completedPages === jobState.snapshot?.totalPages &&
					(jobState.snapshot?.totalPages ?? 0) > 0);
			const isFailed =
				jobState.snapshot?.status === 'failed' ||
				(!jobState.running && jobState.connectionState === 'error' && (jobState.snapshot?.completedPages || 0) === 0);

			if (isDone && lastWatchedStatus !== 'done') {
				lastWatchedStatus = 'done';
				onChapterCompleted(currentChapter, jobState.snapshot);
			} else if (isFailed && lastWatchedStatus !== 'failed') {
				lastWatchedStatus = 'failed';
				onChapterFailed(currentChapter, jobState.lastError || 'Translation failed');
			}
		});
	}

	async function runNextInQueue() {
		if (isProcessingQueue) return;
		isProcessingQueue = true;

		try {
			const state = get({ subscribe });
			if (!state.active || state.status !== 'running') {
				isProcessingQueue = false;
				return;
			}

			if (state.currentIndex >= state.queue.length) {
				finishBatch();
				isProcessingQueue = false;
				return;
			}

			const expectedIndex = state.currentIndex;
			const currentChapter = state.queue[expectedIndex];
			if (!currentChapter) {
				finishBatch();
				isProcessingQueue = false;
				return;
			}

			// ----------------------------------------------------
			// STEP 1: SMART RE-SLICE CHAPTER PAGES (GATED BY USER PREFERENCE)
			// ----------------------------------------------------
			const shouldReslice = (get(settings).resliceBeforeBatch ?? true) && currentChapter.pageCount > 0;
			if (shouldReslice) {
				update((s) => {
					const q = [...s.queue];
					if (q[expectedIndex]) {
						q[expectedIndex] = {
							...q[expectedIndex],
							status: 'reslicing',
							resliceMessage: 'Analyzing canvas & finding optimal speech gutters...',
							error: null,
						};
					}
					const next: BatchTranslationState = {
						...s,
						queue: q,
						currentPhase: 'reslice',
					};
					saveState(next);
					return next;
				});

				currentResliceController = new AbortController();
				const resliceResult = await resliceChapter(
					currentChapter.id,
					(msg) => {
						update((s) => {
							const q = [...s.queue];
							if (q[expectedIndex] && q[expectedIndex].status === 'reslicing') {
								q[expectedIndex] = {
									...q[expectedIndex],
									resliceMessage: msg,
								};
							}
							return { ...s, queue: q };
						});
					},
					currentResliceController.signal,
				);
				currentResliceController = null;

				// Check if batch paused, skipped, or cancelled during reslice
				const stateAfterReslice = get({ subscribe });
				if (!stateAfterReslice.active || stateAfterReslice.status !== 'running' || stateAfterReslice.currentIndex !== expectedIndex) {
					return;
				}

				// Update page count from reslice result if available
				if (resliceResult && resliceResult.newCount > 0) {
					update((s) => {
						const q = [...s.queue];
						if (q[expectedIndex]) {
							q[expectedIndex] = {
								...q[expectedIndex],
								pageCount: resliceResult.newCount,
								totalPages: resliceResult.newCount,
							};
						}
						return { ...s, queue: q };
					});
				}
			}

			// ----------------------------------------------------
			// STEP 2: START PARALLEL CHAPTER TRANSLATION PIPELINE
			// ----------------------------------------------------
			update((s) => {
				const q = [...s.queue];
				if (q[expectedIndex]) {
					q[expectedIndex] = {
						...q[expectedIndex],
						status: 'processing',
						resliceMessage: null,
						error: null,
					};
				}
				const next: BatchTranslationState = {
					...s,
					queue: q,
					currentPhase: 'translate',
				};
				saveState(next);
				return next;
			});

			lastWatchedChapterId = currentChapter.id;
			lastWatchedStatus = 'processing';

			// Launch chapter translation (non-blocking so queue lock is freed while job executes)
			void jobTracker.startTranslation(currentChapter.id, { force: state.force }).catch((err: any) => {
				onChapterFailed(currentChapter, err?.message || 'Failed to start translation');
			});
		} finally {
			isProcessingQueue = false;
		}
	}

	function onChapterCompleted(chapter: BatchChapterItem, snapshot: ChapterJobSnapshot | null) {
		const title = chapter.titleTarget || chapter.title || `Chapter ${chapter.seq + 1}`;
		toast.success(`✓ ${title} translated successfully!`);

		lastWatchedChapterId = null;
		lastWatchedStatus = null;

		update((s) => {
			const q = [...s.queue];
			if (q[s.currentIndex]) {
				q[s.currentIndex] = {
					...q[s.currentIndex],
					status: 'done',
					translatedPages: snapshot?.completedPages || q[s.currentIndex].pageCount,
					totalPages: snapshot?.totalPages || q[s.currentIndex].pageCount,
				};
			}

			const totalCostUsd = s.totalCostUsd + (snapshot?.totalCostUsd || 0);
			const totalPromptTokens = s.totalPromptTokens + (snapshot?.totalPromptTokens || 0);
			const totalCompletionTokens = s.totalCompletionTokens + (snapshot?.totalCompletionTokens || 0);

			const next: BatchTranslationState = {
				...s,
				queue: q,
				currentIndex: s.currentIndex + 1,
				currentPhase: undefined,
				totalCostUsd,
				totalPromptTokens,
				totalCompletionTokens,
			};
			saveState(next);
			return next;
		});

		const nextState = get({ subscribe });
		if (nextState.currentIndex < nextState.queue.length && nextState.status === 'running') {
			void runNextInQueue();
		} else {
			finishBatch();
		}
	}

	function onChapterFailed(chapter: BatchChapterItem, errorMsg: string) {
		const title = chapter.titleTarget || chapter.title || `Chapter ${chapter.seq + 1}`;
		toast.error(`Chapter ${chapter.seq + 1} translation failed: ${errorMsg}`);

		lastWatchedChapterId = null;
		lastWatchedStatus = null;

		update((s) => {
			const q = [...s.queue];
			if (q[s.currentIndex]) {
				q[s.currentIndex] = {
					...q[s.currentIndex],
					status: 'error',
					error: errorMsg,
				};
			}

			const next: BatchTranslationState = {
				...s,
				queue: q,
				currentIndex: s.currentIndex + 1,
				currentPhase: undefined,
			};
			saveState(next);
			return next;
		});

		const nextState = get({ subscribe });
		if (nextState.currentIndex < nextState.queue.length && nextState.status === 'running') {
			void runNextInQueue();
		} else {
			finishBatch();
		}
	}

	function finishBatch() {
		detachJobWatcher();

		update((s) => {
			const next: BatchTranslationState = {
				...s,
				status: 'completed',
				currentPhase: undefined,
				completedAt: Date.now(),
			};
			saveState(next);
			return next;
		});

		const finalState = get({ subscribe });
		const doneCount = finalState.queue.filter((c) => c.status === 'done').length;
		toast.success(`Batch Translation Finished: ${doneCount} of ${finalState.queue.length} chapters complete.`);
	}

	// Initialize and check for auto-resume if page reloads during an active batch
	if (browser) {
		setTimeout(() => {
			const current = get({ subscribe });
			if (current.active && current.status === 'running') {
				attachJobWatcher();
				const currentChapter = current.queue[current.currentIndex];
				if (currentChapter) {
					// Re-attach or continue
					void jobTracker.syncChapter(currentChapter.id).then(() => {
						const jobState = get(jobTracker).jobs[currentChapter.id];
						if (!jobState?.running) {
							void runNextInQueue();
						}
					});
				}
			}
		}, 100);
	}

	return {
		subscribe,

		// Start a new batch
		startBatch(
			bookId: string,
			bookTitle: string,
			chapters: Array<{ id: number; seq: number; title: string; titleTarget?: string | null; pageCount: number }>,
			opts: { force?: boolean } = {},
		) {
			if (chapters.length === 0) return;

			// GUARD: PREVENT RUNNING BATCH ON ANOTHER BOOK WHILE A BATCH IS ALREADY ACTIVE
			const currentState = get({ subscribe });
			if (
				currentState.active &&
				(currentState.status === 'running' || currentState.status === 'paused') &&
				currentState.bookId &&
				currentState.bookId !== bookId
			) {
				const activeBook = currentState.bookTitle || 'another book';
				toast.warning(
					`Batch translation is currently active for "${activeBook}". Please wait for it to finish or stop it before starting another.`,
					{ duration: 5000 },
				);
				return;
			}

			attachJobWatcher();

			const queue: BatchChapterItem[] = chapters.map((ch) => ({
				id: ch.id,
				seq: ch.seq,
				title: ch.title,
				titleTarget: ch.titleTarget,
				pageCount: ch.pageCount,
				status: 'queued',
				translatedPages: 0,
				totalPages: ch.pageCount,
			}));

			const newState: BatchTranslationState = {
				active: true,
				status: 'running',
				bookId,
				bookTitle,
				queue,
				currentIndex: 0,
				currentPhase: 'reslice',
				force: opts.force ?? false,
				startedAt: Date.now(),
				completedAt: null,
				totalCostUsd: 0,
				totalPromptTokens: 0,
				totalCompletionTokens: 0,
			};

			set(newState);
			saveState(newState);

			toast.info(`Starting batch translation for ${chapters.length} chapter${chapters.length === 1 ? '' : 's'} (with smart re-slicing)...`);
			void runNextInQueue();
		},

		// Pause batch (stops triggering next chapter after current finishes)
		pauseBatch() {
			update((s) => {
				const next: BatchTranslationState = { ...s, status: 'paused' };
				saveState(next);
				return next;
			});
			toast.info('Batch translation paused.');
		},

		// Resume paused batch
		resumeBatch() {
			attachJobWatcher();
			update((s) => {
				const next: BatchTranslationState = { ...s, status: 'running' };
				saveState(next);
				return next;
			});
			toast.info('Resuming batch translation...');
			void runNextInQueue();
		},

		// Skip currently processing chapter and move to next
		async skipCurrentChapter() {
			if (currentResliceController) {
				currentResliceController.abort();
				currentResliceController = null;
			}

			const state = get({ subscribe });
			const current = state.queue[state.currentIndex];
			if (!current) return;

			try {
				await jobTracker.cancelTranslation(current.id);
			} catch {
				// Ignore
			}

			lastWatchedChapterId = null;
			lastWatchedStatus = null;

			update((s) => {
				const q = [...s.queue];
				if (q[s.currentIndex]) {
					q[s.currentIndex] = {
						...q[s.currentIndex],
						status: 'skipped',
						error: 'Skipped by user',
					};
				}
				const next: BatchTranslationState = {
					...s,
					queue: q,
					currentIndex: s.currentIndex + 1,
					currentPhase: undefined,
				};
				saveState(next);
				return next;
			});

			toast.info(`Skipped Chapter ${current.seq + 1}.`);
			void runNextInQueue();
		},

		// Cancel the entire batch translation
		async cancelBatch() {
			if (currentResliceController) {
				currentResliceController.abort();
				currentResliceController = null;
			}

			detachJobWatcher();

			const state = get({ subscribe });
			const current = state.queue[state.currentIndex];
			if (current && (current.status === 'processing' || current.status === 'reslicing')) {
				try {
					await jobTracker.cancelTranslation(current.id);
				} catch {
					// Ignore
				}
			}

			update((s) => {
				const updatedQueue = s.queue.map((item, idx) => {
					if (idx === s.currentIndex && (item.status === 'processing' || item.status === 'reslicing')) {
						return {
							...item,
							status: 'cancelled' as const,
							error: 'Cancelled by user',
							resliceMessage: null,
						};
					}
					if (item.status === 'queued') {
						return {
							...item,
							status: 'cancelled' as const,
							error: 'Batch cancelled',
							resliceMessage: null,
						};
					}
					return item;
				});

				const next: BatchTranslationState = {
					...s,
					queue: updatedQueue,
					status: 'cancelled',
					currentPhase: undefined,
					completedAt: Date.now(),
				};
				saveState(next);
				return next;
			});

			toast.info('Batch translation cancelled.');
		},

		// Dismiss / Clear finished or cancelled batch from view
		clearBatch() {
			detachJobWatcher();
			const next: BatchTranslationState = {
				...initialBatchState,
			};
			set(next);
			saveState(next);
		},

		// Manually re-sync state on mount
		sync() {
			const state = get({ subscribe });
			if (state.active && state.status === 'running') {
				attachJobWatcher();
				const current = state.queue[state.currentIndex];
				if (current) {
					void jobTracker.syncChapter(current.id);
				}
			}
		},
	};
}

export const batchTracker = createBatchTrackerStore();

// Derived helper for aggregated progress metrics
export const batchProgress = derived(
	[batchTracker, jobTracker],
	([$bt, $jt]) => {
		if (!$bt.active || $bt.queue.length === 0) {
			return {
				active: false,
				totalChapters: 0,
				completedChapters: 0,
				failedChapters: 0,
				totalAllPages: 0,
				completedAllPages: 0,
				overallProgressPercent: 0,
				currentChapter: null,
				currentJobState: null,
			};
		}

		const totalChapters = $bt.queue.length;
		const completedChapters = $bt.queue.filter((c) => c.status === 'done').length;
		const failedChapters = $bt.queue.filter((c) => c.status === 'error').length;
		const processedChapters = $bt.queue.filter((c) => c.status === 'done' || c.status === 'skipped' || c.status === 'error').length;

		let totalAllPages = 0;
		let completedAllPages = 0;

		for (let i = 0; i < $bt.queue.length; i++) {
			const item = $bt.queue[i];
			const count = item.pageCount || item.totalPages || 0;
			totalAllPages += count;

			if (item.status === 'done') {
				completedAllPages += count;
			} else if (item.status === 'processing') {
				const jobState = $jt.jobs[item.id];
				const doneInJob = jobState?.snapshot?.completedPages || item.translatedPages || 0;
				completedAllPages += doneInJob;
			}
		}

		const overallProgressPercent =
			$bt.status === 'completed'
				? 100
				: totalAllPages > 0
					? Math.min(100, Math.round((completedAllPages / totalAllPages) * 100))
					: Math.min(100, Math.round((processedChapters / totalChapters) * 100));

		const currentChapter = $bt.queue[$bt.currentIndex] || null;
		const currentJobState = currentChapter ? $jt.jobs[currentChapter.id] || null : null;

		return {
			active: $bt.active,
			status: $bt.status,
			currentPhase: $bt.currentPhase,
			totalChapters,
			completedChapters,
			failedChapters,
			totalAllPages,
			completedAllPages,
			overallProgressPercent,
			currentChapter,
			currentJobState,
		};
	},
);
