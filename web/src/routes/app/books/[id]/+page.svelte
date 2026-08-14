<script lang="ts">
	// IMPORTED DEP-COMPONENTS
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button, TextField, Badge, Modal, ConfirmDialog, ActionMenu, LanguagePicker, Toggle, LazyImage } from '$lib/components/ui';
	import { ripple } from '$lib/actions/ripple';
	// IMPORTED ICONS
	import ArrowLeft from 'lucide-svelte/icons/arrow-left';
	import Plus from 'lucide-svelte/icons/plus';
	import Search from 'lucide-svelte/icons/search';
	import Trash2 from 'lucide-svelte/icons/trash-2';
	import ExternalLink from 'lucide-svelte/icons/external-link';
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Layers from 'lucide-svelte/icons/layers';
	import Pencil from 'lucide-svelte/icons/pencil';
	import ArrowUpDown from 'lucide-svelte/icons/arrow-up-down';
	import Pin from 'lucide-svelte/icons/pin';
	import Play from 'lucide-svelte/icons/play';
	import Download from 'lucide-svelte/icons/download';
	import Hash from 'lucide-svelte/icons/hash';
	import LayoutGrid from 'lucide-svelte/icons/layout-grid';
	import List from 'lucide-svelte/icons/list';
	import AlignJustify from 'lucide-svelte/icons/align-justify';

	// -- TYPES -- //

	interface Book {
		id: string;
		title: string;
		titleTarget?: string | null;
		sourceLang: string;
		targetLang: string;
		pinned?: boolean;
		archived?: boolean;
	}

	interface Chapter {
		id: number;
		title: string;
		titleTarget?: string | null;
		seq: number;
		status: 'pending' | 'processing' | 'done' | 'error';
		pageCount: number;
		translatedPageCount?: number;
		coverPageId?: number | null;
		coverHasOutput?: boolean;
		translatedAt?: number;
	}

	// -- STATES -- //

	let book: Book | null = null;
	let chapters: Chapter[] = [];
	let loading = true;
	let chapterTitle = '';
	let chapterTitleTarget = '';
	let creating = false;
	let searchQuery = '';
	let createModalOpen = false;
	let sortAscending = true;
	let statusFilter: 'all' | 'done' | 'pending' | 'error' = 'all';

	// VIEW LAYOUT MODES: 'grid' (Comfortable Cards) | 'list' (Media List Rows) | 'compact' (Dense Table Rows)
	let viewLayout: 'grid' | 'list' | 'compact' = 'grid';

	// PERFORMANCE / WINDOWING STATES FOR THOUSANDS OF CHAPTERS
	let visibleLimit = 36;
	let jumpInput = '';

	// EDIT BOOK STATES
	let editBookModalOpen = false;
	let editBookTitle = '';
	let editBookTitleTarget = '';
	let editBookSourceLang = '';
	let editBookTargetLang = '';
	let editBookPinned = false;
	let editBookArchived = false;
	let updatingBook = false;

	// EDIT CHAPTER STATES
	let editChapterModalOpen = false;
	let editingChapter: Chapter | null = null;
	let editChapterTitle = '';
	let editChapterTitleTarget = '';
	let editChapterSeq = 1;
	let updatingChapter = false;

	// DELETION STATES
	let chapterToDelete: Chapter | null = null;
	let deleteConfirmOpen = false;
	let deleting = false;

	// -- LIFECYCLES -- //

	onMount(async () => {
		try {
			const saved = localStorage.getItem('manhua:chapterViewLayout');
			if (saved === 'grid' || saved === 'list' || saved === 'compact') {
				viewLayout = saved;
			}
		} catch {
			// ignore
		}
		await reload();
	});

	function setViewLayout(mode: 'grid' | 'list' | 'compact') {
		viewLayout = mode;
		try {
			localStorage.setItem('manhua:chapterViewLayout', mode);
		} catch {
			// ignore
		}
	}

	// -- FUNCTIONS -- //

	async function reload() {
		try {
			const resp = await fetch(`/api/books/${$page.params.id}`);
			if (!resp.ok) throw new Error('not found');
			const data = await resp.json();
			book = data.book;
			chapters = data.chapters;
		} catch {
			toast.error('Could not load the book.');
		} finally {
			loading = false;
		}
	}

	function openEditBookModal() {
		if (!book) return;
		editBookTitle = book.title;
		editBookTitleTarget = book.titleTarget || '';
		editBookSourceLang = book.sourceLang;
		editBookTargetLang = book.targetLang;
		editBookPinned = !!book.pinned;
		editBookArchived = !!book.archived;
		editBookModalOpen = true;
	}

	async function updateBook() {
		if (!book) return;
		const t = editBookTitle.trim();
		if (!t) return;
		updatingBook = true;
		try {
			const resp = await fetch(`/api/books/${book.id}`, {
				method: 'PATCH',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					title: t,
					titleTarget: editBookTitleTarget.trim() || null,
					sourceLang: editBookSourceLang,
					targetLang: editBookTargetLang,
					pinned: editBookPinned,
					archived: editBookArchived,
				}),
			});
			if (!resp.ok) throw new Error('Update failed');
			const data = await resp.json();
			book = data.book;
			toast.success('Book details updated.');
			editBookModalOpen = false;
		} catch {
			toast.error('Could not update book details.');
		} finally {
			updatingBook = false;
		}
	}

	async function createChapter() {
		creating = true;
		try {
			const resp = await fetch(`/api/books/${$page.params.id}/chapters`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					title: chapterTitle.trim(),
					titleTarget: chapterTitleTarget.trim() || undefined,
				}),
			});
			if (!resp.ok) throw new Error('create failed');
			const { id: chapterId } = await resp.json();
			toast.success('Chapter created.');
			chapterTitle = '';
			chapterTitleTarget = '';
			createModalOpen = false;
			goto(`/app/books/${$page.params.id}/chapters/${chapterId}/`);
		} catch {
			toast.error('Could not create the chapter.');
		} finally {
			creating = false;
		}
	}

	function openEditChapterModal(chapter: Chapter) {
		editingChapter = chapter;
		editChapterTitle = chapter.title;
		editChapterTitleTarget = chapter.titleTarget || '';
		editChapterSeq = chapter.seq + 1;
		editChapterModalOpen = true;
	}

	async function updateChapter() {
		if (!editingChapter) return;
		updatingChapter = true;
		try {
			const resp = await fetch(`/api/chapters/${editingChapter.id}`, {
				method: 'PATCH',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					title: editChapterTitle.trim(),
					titleTarget: editChapterTitleTarget.trim() || null,
					seq: Math.max(0, editChapterSeq - 1),
				}),
			});
			if (!resp.ok) throw new Error('Update failed');
			const data = await resp.json();
			const updated = data.chapter;
			chapters = chapters.map((c) =>
				c.id === updated.id ? { ...c, ...updated } : c,
			);
			toast.success('Chapter updated.');
			editChapterModalOpen = false;
			editingChapter = null;
		} catch {
			toast.error('Could not update chapter.');
		} finally {
			updatingChapter = false;
		}
	}

	function promptDeleteChapter(chap: Chapter) {
		chapterToDelete = chap;
		deleteConfirmOpen = true;
	}

	async function confirmDeleteChapter() {
		if (!chapterToDelete) return;
		deleting = true;
		try {
			const resp = await fetch(`/api/chapters/${chapterToDelete.id}`, { method: 'DELETE' });
			if (!resp.ok) throw new Error('Delete failed');
			toast.success('Chapter deleted.');
			chapters = chapters.filter((c) => c.id !== chapterToDelete?.id);
		} catch {
			toast.error('Could not delete chapter.');
		} finally {
			deleting = false;
			deleteConfirmOpen = false;
			chapterToDelete = null;
		}
	}

	function getChapterProgress(ch: Chapter): { percent: number; isComplete: boolean } {
		const total = ch.pageCount || 0;
		const done = ch.translatedPageCount || 0;
		if (total === 0) return { percent: ch.status === 'done' ? 100 : 0, isComplete: ch.status === 'done' };
		const percent = Math.min(100, Math.round((done / total) * 100));
		return { percent, isComplete: ch.status === 'done' || (total > 0 && done === total) };
	}

	function loadMore() {
		visibleLimit += 36;
	}

	function jumpToChapter() {
		const targetSeq = parseInt(jumpInput, 10);
		if (!targetSeq || isNaN(targetSeq)) return;
		const idx = filteredChapters.findIndex((c) => c.seq + 1 === targetSeq);
		if (idx !== -1) {
			if (idx >= visibleLimit) {
				visibleLimit = Math.min(filteredChapters.length, idx + 24);
			}
			setTimeout(() => {
				const el = document.getElementById(`chapter-card-${filteredChapters[idx].id}`);
				if (el) {
					el.scrollIntoView({ behavior: 'smooth', block: 'center' });
					el.classList.add('ring-2', 'ring-[#b23a2e]');
					setTimeout(() => el.classList.remove('ring-2', 'ring-[#b23a2e]'), 2000);
				}
			}, 60);
		} else {
			toast.error(`Chapter #${targetSeq} not found in current filter.`);
		}
	}

	const statusVariant: Record<Chapter['status'], 'neutral' | 'amber' | 'jade' | 'cinnabar'> = {
		pending: 'neutral',
		processing: 'amber',
		done: 'jade',
		error: 'cinnabar',
	};

	$: filteredChapters = chapters
		.filter((c) => {
			if (statusFilter === 'done' && c.status !== 'done') return false;
			if (statusFilter === 'pending' && c.status !== 'pending' && c.status !== 'processing') return false;
			if (statusFilter === 'error' && c.status !== 'error') return false;

			if (!searchQuery.trim()) return true;
			const q = searchQuery.toLowerCase();
			return (
				(c.title || `Chapter ${c.seq + 1}`).toLowerCase().includes(q) ||
				(c.titleTarget && c.titleTarget.toLowerCase().includes(q)) ||
				c.status.toLowerCase().includes(q)
			);
		})
		.sort((a, b) => (sortAscending ? a.seq - b.seq : b.seq - a.seq));

	$: displayedChapters = filteredChapters.slice(0, visibleLimit);
	$: hasMore = visibleLimit < filteredChapters.length;

	$: totalPages = chapters.reduce((sum, c) => sum + (c.pageCount || 0), 0);
	$: translatedPages = chapters.reduce((sum, c) => sum + (c.translatedPageCount || 0), 0);
	$: translatedChapters = chapters.filter((c) => c.status === 'done' || (c.pageCount > 0 && c.translatedPageCount === c.pageCount)).length;
	$: overallProgress = totalPages > 0 ? Math.round((translatedPages / totalPages) * 100) : (chapters.length > 0 ? Math.round((translatedChapters / chapters.length) * 100) : 0);
	$: bookCoverPageId = chapters.find((c) => c.coverPageId)?.coverPageId ?? null;
</script>

<svelte:head>
	<title>{book ? `${book.title} — Manhua Translator` : 'Book Details'}</title>
</svelte:head>

<!-- BOOK DETAIL & CHAPTER MANAGEMENT -->
<div class="flex flex-col gap-6">
	<!-- BACK BUTTON -->
	<div>
		<a
			href="/app/"
			class="inline-flex items-center gap-1.5 text-xs font-semibold opacity-60 transition hover:opacity-100 hover:text-[#b23a2e]"
			use:ripple
		>
			<ArrowLeft size={14} /> Back to Library
		</a>
	</div>

	{#if loading}
		<div class="h-48 animate-pulse rounded-2xl border border-black/[0.06] bg-black/[0.03] dark:border-white/[0.06] dark:bg-white/[0.03]"></div>
	{:else if book}
		<!-- HERO HEADER CARD -->
		<div class="relative overflow-hidden rounded-2xl border border-black/[0.08] bg-white/60 p-6 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.02]">
			<div class="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
				<div class="flex gap-5 min-w-0 flex-1">
					<!-- BOOK COVER PREVIEW IN HEADER (SM+) -->
					{#if bookCoverPageId}
						<div class="hidden sm:block w-24 shrink-0">
							<LazyImage
								src={`/api/pages/${bookCoverPageId}/file?kind=thumb&w=280`}
								alt={`${book.title} Cover`}
								fallbackText={book.title.slice(0, 1) || '书'}
								aspectRatio="aspect-[2/3]"
								showSpineShadow={true}
							/>
						</div>
					{/if}

					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-2 flex-wrap">
							{#if book.pinned}
								<span title="Pinned Series" class="flex items-center text-amber-600 dark:text-amber-400">
									<Pin size={16} class="rotate-45 fill-current" />
								</span>
							{/if}
							<h1 class="text-2xl font-bold tracking-tight sm:text-3xl">{book.title}</h1>
							{#if book.titleTarget}
								<span class="text-lg font-medium opacity-70">({book.titleTarget})</span>
							{/if}
							<span class="rounded-md bg-[#b23a2e]/10 px-2.5 py-0.5 text-xs font-semibold text-[#b23a2e] dark:text-[#e08a63]">
								{book.sourceLang} → {book.targetLang}
							</span>
							{#if book.archived}
								<span class="rounded-md bg-black/10 dark:bg-white/10 px-2 py-0.5 text-xs font-semibold opacity-60">
									Archived
								</span>
							{/if}
						</div>

						<p class="mt-1.5 text-sm opacity-60">
							{chapters.length} chapter{chapters.length === 1 ? '' : 's'} · {translatedChapters} translated ({translatedPages}/{totalPages} pages)
						</p>

						<!-- OVERALL PROGRESS BAR -->
						{#if chapters.length > 0}
							<div class="mt-4 max-w-md">
								<div class="flex items-center justify-between text-xs mb-1 font-medium">
									<span class="opacity-60">Series Completion</span>
									<span class="font-mono text-xs">{overallProgress}%</span>
								</div>
								<div class="h-2 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
									<div
										class={`h-full rounded-full transition-all duration-500 ${
											overallProgress === 100
												? 'bg-emerald-600 dark:bg-emerald-400'
												: 'bg-[#b23a2e] dark:bg-[#e08a63]'
										}`}
										style={`width: ${overallProgress}%`}
									></div>
								</div>
							</div>
						{/if}
					</div>
				</div>

				<div class="flex flex-wrap items-center gap-2.5 shrink-0">
					<Button variant="secondary" on:click={openEditBookModal}>
						<Pencil size={15} /> Edit Book
					</Button>
					<Button variant="secondary" on:click={() => goto(`/app/glossary/?bookId=${book?.id}`)}>
						<BookOpen size={15} /> Glossary
					</Button>
					<Button variant="primary" on:click={() => (createModalOpen = true)}>
						<Plus size={15} /> New Chapter
					</Button>
				</div>
			</div>
		</div>

		<!-- SEARCH & TOOLBAR WITH VIEW MODES, SORTING, FILTERS, AND CHAPTER JUMP -->
		<div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
			<!-- STATUS FILTER PILLS -->
			<div class="flex items-center gap-1 rounded-xl bg-black/[0.04] p-1 dark:bg-white/[0.04]">
				<button
					type="button"
					on:click={() => (statusFilter = 'all')}
					class={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
						statusFilter === 'all'
							? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					use:ripple
				>
					All ({chapters.length})
				</button>
				<button
					type="button"
					on:click={() => (statusFilter = 'done')}
					class={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
						statusFilter === 'done'
							? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					use:ripple
				>
					Translated ({translatedChapters})
				</button>
				<button
					type="button"
					on:click={() => (statusFilter = 'pending')}
					class={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
						statusFilter === 'pending'
							? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
							: 'opacity-60 hover:opacity-100'
					}`}
					use:ripple
				>
					Pending ({chapters.filter((c) => c.status === 'pending' || c.status === 'processing').length})
				</button>
			</div>

			<!-- CONTROLS: VIEW SWITCHER, SORT, JUMP & SEARCH -->
			<div class="flex flex-wrap items-center gap-2">
				<!-- VIEW MODE SWITCHER SEGMENTED TABS -->
				<div class="flex items-center gap-0.5 rounded-xl border border-black/10 bg-black/[0.03] p-1 dark:border-white/10 dark:bg-white/[0.03]">
					<button
						type="button"
						on:click={() => setViewLayout('grid')}
						class={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs transition-all ${
							viewLayout === 'grid'
								? 'bg-white text-black font-bold shadow-xs dark:bg-[#221e1a] dark:text-white'
								: 'opacity-60 hover:opacity-100'
						}`}
						title="Comfortable Cards Grid"
						use:ripple
					>
						<LayoutGrid size={13} />
						<span class="hidden sm:inline">Grid</span>
					</button>

					<button
						type="button"
						on:click={() => setViewLayout('list')}
						class={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs transition-all ${
							viewLayout === 'list'
								? 'bg-white text-black font-bold shadow-xs dark:bg-[#221e1a] dark:text-white'
								: 'opacity-60 hover:opacity-100'
						}`}
						title="Media List Rows"
						use:ripple
					>
						<List size={13} />
						<span class="hidden sm:inline">List</span>
					</button>

					<button
						type="button"
						on:click={() => setViewLayout('compact')}
						class={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs transition-all ${
							viewLayout === 'compact'
								? 'bg-white text-black font-bold shadow-xs dark:bg-[#221e1a] dark:text-white'
								: 'opacity-60 hover:opacity-100'
						}`}
						title="Compact Table Rows"
						use:ripple
					>
						<AlignJustify size={13} />
						<span class="hidden sm:inline">Compact</span>
					</button>
				</div>

				<!-- SORT ORDER BUTTON -->
				<button
					type="button"
					on:click={() => (sortAscending = !sortAscending)}
					class="flex items-center gap-1.5 rounded-xl border border-black/10 px-3 py-1.5 text-xs font-medium transition hover:bg-black/5 dark:border-white/10 dark:hover:bg-white/5"
					title="Toggle chapter sorting"
					use:ripple
				>
					<ArrowUpDown size={13} />
					<span>{sortAscending ? 'Oldest' : 'Newest'}</span>
				</button>

				<!-- FAST JUMP TO CHAPTER # -->
				{#if chapters.length > 20}
					<form on:submit|preventDefault={jumpToChapter} class="relative flex items-center">
						<Hash size={13} class="pointer-events-none absolute left-2.5 opacity-40" />
						<input
							type="number"
							min="1"
							max={chapters.length}
							bind:value={jumpInput}
							placeholder="Jump #"
							class="w-24 rounded-xl border border-black/10 bg-transparent py-1.5 pl-7 pr-2 text-xs outline-none transition placeholder:opacity-40 focus:border-[#b23a2e] dark:border-white/10"
						/>
					</form>
				{/if}

				<!-- SEARCH BAR -->
				<div class="relative min-w-0 flex-1 max-w-xs">
					<Search size={14} class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
					<input
						bind:value={searchQuery}
						type="search"
						placeholder="Search chapters..."
						class="w-full rounded-xl border border-black/10 bg-transparent py-1.5 pl-8 pr-3 text-xs sm:text-sm outline-none transition placeholder:opacity-40 focus:border-[#b23a2e] dark:border-white/10"
					/>
				</div>
			</div>
		</div>

		<!-- CHAPTER LISTINGS -->
		{#if chapters.length === 0}
			<div class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-black/15 py-16 text-center dark:border-white/15">
				<div class="flex h-12 w-12 items-center justify-center rounded-full bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]">
					<Layers size={24} />
				</div>
				<h2 class="mt-4 text-base font-semibold">No chapters yet</h2>
				<p class="mt-1 max-w-sm text-xs opacity-60">Create your first chapter to start uploading page images for text detection & translation.</p>
				<Button variant="primary" size="sm" class="mt-4" on:click={() => (createModalOpen = true)}>
					<Plus size={14} /> Create Chapter
				</Button>
			</div>
		{:else if filteredChapters.length === 0}
			<p class="py-8 text-center text-sm opacity-60">No chapters found matching "{searchQuery}".</p>
		{:else if viewLayout === 'grid'}
			<!-- MODE 1: COMFORTABLE 2-COLUMN CARDS GRID -->
			<ul class="grid w-full gap-5 sm:grid-cols-2">
				{#each displayedChapters as chapter (chapter.id)}
					{@const chProgress = getChapterProgress(chapter)}
					<li
						id={`chapter-card-${chapter.id}`}
						data-chapter-seq={chapter.seq + 1}
						class="group relative flex flex-col justify-between rounded-2xl border border-black/[0.08] bg-white/60 p-4 transition-all duration-300 hover:border-[#b23a2e]/40 hover:shadow-xl dark:border-white/[0.06] dark:bg-white/[0.02]"
					>
						<!-- UPPER SECTION: MINI PAGE THUMBNAIL + CHAPTER INFO -->
						<div class="flex gap-3.5">
							<!-- 2:3 VERTICAL CHAPTER COVER THUMBNAIL -->
							<a
								href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
								class="group/cover w-20 sm:w-24 shrink-0 transition-transform duration-300 hover:scale-102"
								title={`Open ${chapter.title || `Chapter ${chapter.seq + 1}`}`}
							>
								<LazyImage
									src={chapter.coverPageId ? `/api/pages/${chapter.coverPageId}/file?kind=thumb&w=260` : ''}
									alt={chapter.title || `Chapter ${chapter.seq + 1}`}
									fallbackText={`Ch.${chapter.seq + 1}`}
									aspectRatio="aspect-[2/3]"
									showSpineShadow={true}
								/>
							</a>

							<!-- CHAPTER DETAILS -->
							<div class="min-w-0 flex-1 flex flex-col justify-between">
								<div>
									<div class="flex items-start justify-between gap-1.5">
										<div class="min-w-0 flex-1">
											<a
												href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
												class="font-bold text-base tracking-tight hover:text-[#b23a2e] dark:hover:text-[#e08a63] block truncate"
												title={chapter.title || `Chapter ${chapter.seq + 1}`}
											>
												{chapter.title || `Chapter ${chapter.seq + 1}`}
											</a>
											{#if chapter.titleTarget}
												<p class="text-xs opacity-60 font-medium truncate mt-0.5" title={chapter.titleTarget}>
													{chapter.titleTarget}
												</p>
											{/if}
										</div>

										<ActionMenu
											items={[
												{ value: 'open', label: 'Open Reader', icon: ExternalLink },
												{ value: 'edit', label: 'Edit Chapter Details', icon: Pencil },
												{ value: 'delete', label: 'Delete Chapter', icon: Trash2, danger: true },
											]}
											on:select={(e) => {
												if (e.detail === 'open') goto(`/app/books/${$page.params.id}/chapters/${chapter.id}/`);
												else if (e.detail === 'edit') openEditChapterModal(chapter);
												else if (e.detail === 'delete') promptDeleteChapter(chapter);
											}}
										/>
									</div>

									<!-- STATUS & PAGE BADGES -->
									<div class="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
										<Badge variant={statusVariant[chapter.status]}>
											{chapter.status.toUpperCase()}
										</Badge>
										<span class="rounded-md bg-black/5 dark:bg-white/5 px-2 py-0.5 font-medium opacity-70">
											{chapter.pageCount} {chapter.pageCount === 1 ? 'page' : 'pages'}
										</span>
									</div>
								</div>

								<!-- CHAPTER PAGE PROGRESS BAR -->
								<div class="mt-2.5">
									<div class="flex items-center justify-between text-[11px] mb-1">
										<span class="opacity-60 text-[10px] font-medium">
											{#if chProgress.isComplete}
												<span class="text-emerald-600 dark:text-emerald-400 font-semibold">✓ Translated</span>
											{:else if chapter.status === 'processing'}
												<span class="text-amber-600 dark:text-amber-400 font-semibold">Translating...</span>
											{:else}
												{chapter.translatedPageCount || 0}/{chapter.pageCount} pages ({chProgress.percent}%)
											{/if}
										</span>
										<span class="opacity-40 text-[10px] font-mono">Seq #{chapter.seq + 1}</span>
									</div>
									<div class="h-1.5 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
										<div
											class={`h-full rounded-full transition-all duration-500 ${
												chProgress.isComplete
													? 'bg-emerald-600 dark:bg-emerald-400'
													: 'bg-[#b23a2e] dark:bg-[#e08a63]'
											}`}
											style={`width: ${chProgress.percent}%`}
										></div>
									</div>
								</div>
							</div>
						</div>

						<!-- LOWER SECTION: ACTION FOOTER BAR -->
						<div class="mt-3.5 flex items-center justify-between border-t border-black/[0.05] pt-2.5 text-xs dark:border-white/[0.05]">
							<a
								href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
								class="inline-flex items-center gap-1.5 rounded-lg bg-[#b23a2e]/10 px-2.5 py-1 font-semibold text-[#b23a2e] transition hover:bg-[#b23a2e] hover:text-white dark:text-[#e08a63] dark:hover:bg-[#e08a63] dark:hover:text-black"
								use:ripple
							>
								<Play size={11} class="fill-current" />
								<span>Open Reader</span>
							</a>

							{#if chapter.pageCount > 0}
								<a
									href={`/api/chapters/${chapter.id}/download`}
									class="inline-flex items-center gap-1 opacity-60 hover:opacity-100 transition hover:text-[#b23a2e]"
									download
									title="Export Chapter ZIP"
								>
									<Download size={13} />
									<span class="text-[11px]">ZIP</span>
								</a>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		{:else if viewLayout === 'list'}
			<!-- MODE 2: MEDIA LIST STRIP (HORIZONTAL ROWS) -->
			<ul class="flex flex-col gap-2.5 w-full">
				{#each displayedChapters as chapter (chapter.id)}
					{@const chProgress = getChapterProgress(chapter)}
					<li
						id={`chapter-card-${chapter.id}`}
						data-chapter-seq={chapter.seq + 1}
						class="group flex items-center justify-between gap-4 rounded-xl border border-black/[0.07] bg-white/60 p-3 transition-all hover:border-[#b23a2e]/40 hover:bg-white hover:shadow-md dark:border-white/[0.06] dark:bg-white/[0.02] dark:hover:bg-white/[0.04]"
					>
						<div class="flex items-center gap-3.5 min-w-0 flex-1">
							<!-- 40px MINI THUMBNAIL -->
							<a
								href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
								class="w-10 sm:w-12 shrink-0 transition-transform duration-200 group-hover:scale-105"
								title={`Open ${chapter.title || `Chapter ${chapter.seq + 1}`}`}
							>
								<LazyImage
									src={chapter.coverPageId ? `/api/pages/${chapter.coverPageId}/file?kind=thumb&w=140` : ''}
									alt={chapter.title || `Chapter ${chapter.seq + 1}`}
									fallbackText={`#${chapter.seq + 1}`}
									aspectRatio="aspect-[2/3]"
									showSpineShadow={false}
									class="rounded-lg shadow-2xs"
								/>
							</a>

							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2 flex-wrap">
									<span class="rounded bg-black/5 dark:bg-white/5 px-1.5 py-0.5 font-mono text-[10px] font-bold opacity-60">
										#{chapter.seq + 1}
									</span>
									<a
										href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
										class="font-bold text-sm hover:text-[#b23a2e] dark:hover:text-[#e08a63] truncate"
										title={chapter.title || `Chapter ${chapter.seq + 1}`}
									>
										{chapter.title || `Chapter ${chapter.seq + 1}`}
									</a>
									{#if chapter.titleTarget}
										<span class="text-xs opacity-60 font-medium truncate hidden sm:inline" title={chapter.titleTarget}>
											({chapter.titleTarget})
										</span>
									{/if}
								</div>

								<div class="mt-1 flex items-center gap-2.5 text-[11px] opacity-65">
									<span>{chapter.pageCount} pages</span>
									<span>•</span>
									<span class={chProgress.isComplete ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}>
										{chProgress.isComplete ? '100% Translated' : `${chapter.translatedPageCount || 0}/${chapter.pageCount} translated`}
									</span>
								</div>
							</div>
						</div>

						<div class="flex items-center gap-2.5 shrink-0">
							<Badge variant={statusVariant[chapter.status]} class="hidden sm:inline-flex">
								{chapter.status.toUpperCase()}
							</Badge>

							<a
								href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
								class="inline-flex items-center gap-1 rounded-lg bg-[#b23a2e]/10 px-2.5 py-1 text-xs font-semibold text-[#b23a2e] transition hover:bg-[#b23a2e] hover:text-white dark:text-[#e08a63] dark:hover:bg-[#e08a63] dark:hover:text-black"
								use:ripple
							>
								<Play size={11} class="fill-current" />
								<span>Read</span>
							</a>

							{#if chapter.pageCount > 0}
								<a
									href={`/api/chapters/${chapter.id}/download`}
									class="hidden sm:inline-flex items-center justify-center p-1.5 opacity-60 hover:opacity-100 hover:text-[#b23a2e]"
									download
									title="Download ZIP"
								>
									<Download size={14} />
								</a>
							{/if}

							<ActionMenu
								items={[
									{ value: 'open', label: 'Open Reader', icon: ExternalLink },
									{ value: 'edit', label: 'Edit Chapter Details', icon: Pencil },
									{ value: 'delete', label: 'Delete Chapter', icon: Trash2, danger: true },
								]}
								on:select={(e) => {
									if (e.detail === 'open') goto(`/app/books/${$page.params.id}/chapters/${chapter.id}/`);
									else if (e.detail === 'edit') openEditChapterModal(chapter);
									else if (e.detail === 'delete') promptDeleteChapter(chapter);
								}}
							/>
						</div>
					</li>
				{/each}
			</ul>
		{:else}
			<!-- MODE 3: DENSE TABLE / COMPACT ROWS (FOR POWER SCROLLING) -->
			<div class="overflow-hidden rounded-xl border border-black/[0.08] bg-white/60 shadow-xs dark:border-white/[0.06] dark:bg-white/[0.02]">
				<table class="w-full text-left text-xs border-collapse">
					<thead>
						<tr class="border-b border-black/[0.06] bg-black/[0.02] text-[11px] font-semibold opacity-60 dark:border-white/[0.06] dark:bg-white/[0.02]">
							<th class="py-2.5 pl-4 pr-2 w-14">#</th>
							<th class="py-2.5 px-3">Chapter Title</th>
							<th class="py-2.5 px-3 hidden md:table-cell">Translated Subtitle</th>
							<th class="py-2.5 px-3 w-24">Pages</th>
							<th class="py-2.5 px-3 w-28">Status</th>
							<th class="py-2.5 pr-4 pl-3 w-24 text-right">Actions</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-black/[0.04] dark:divide-white/[0.04]">
						{#each displayedChapters as chapter (chapter.id)}
							{@const chProgress = getChapterProgress(chapter)}
							<tr
								id={`chapter-card-${chapter.id}`}
								data-chapter-seq={chapter.seq + 1}
								class="group transition hover:bg-black/[0.02] dark:hover:bg-white/[0.02]"
							>
								<td class="py-2 pl-4 pr-2 font-mono font-bold opacity-60">
									#{chapter.seq + 1}
								</td>
								<td class="py-2 px-3 font-semibold">
									<a
										href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
										class="hover:text-[#b23a2e] dark:hover:text-[#e08a63]"
									>
										{chapter.title || `Chapter ${chapter.seq + 1}`}
									</a>
								</td>
								<td class="py-2 px-3 opacity-60 hidden md:table-cell truncate max-w-xs">
									{chapter.titleTarget || '—'}
								</td>
								<td class="py-2 px-3 font-mono opacity-70">
									{chapter.translatedPageCount || 0}/{chapter.pageCount}
								</td>
								<td class="py-2 px-3">
									<Badge variant={statusVariant[chapter.status]}>
										{chapter.status.toUpperCase()}
									</Badge>
								</td>
								<td class="py-2 pr-4 pl-3 text-right">
									<div class="flex items-center justify-end gap-1.5">
										<a
											href={`/app/books/${$page.params.id}/chapters/${chapter.id}/`}
											class="p-1 rounded opacity-70 hover:opacity-100 hover:text-[#b23a2e]"
											title="Open Reader"
										>
											<Play size={13} class="fill-current" />
										</a>
										<ActionMenu
											items={[
												{ value: 'open', label: 'Open Reader', icon: ExternalLink },
												{ value: 'edit', label: 'Edit Chapter Details', icon: Pencil },
												{ value: 'delete', label: 'Delete Chapter', icon: Trash2, danger: true },
											]}
											on:select={(e) => {
												if (e.detail === 'open') goto(`/app/books/${$page.params.id}/chapters/${chapter.id}/`);
												else if (e.detail === 'edit') openEditChapterModal(chapter);
												else if (e.detail === 'delete') promptDeleteChapter(chapter);
											}}
										/>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		<!-- PROGRESSIVE LOAD MORE -->
		{#if hasMore}
			<div class="flex flex-col items-center justify-center gap-2 py-8">
				<Button variant="secondary" on:click={loadMore}>
					Load More Chapters ({filteredChapters.length - visibleLimit} remaining)
				</Button>
				<span class="text-xs opacity-40">
					Showing {displayedChapters.length} of {filteredChapters.length} chapters
				</span>
			</div>
		{/if}
	{/if}
</div>

<!-- CREATE CHAPTER MODAL -->
<Modal open={createModalOpen} title="Create New Chapter" size="sm" on:close={() => (createModalOpen = false)}>
	<form class="flex flex-col gap-4" on:submit|preventDefault={createChapter}>
		<TextField
			bind:value={chapterTitle}
			label="Chapter Title (Source Language)"
			placeholder="e.g. 第1话"
		/>

		<TextField
			bind:value={chapterTitleTarget}
			label="Target Title (Optional translation)"
			placeholder="e.g. Chapter 1: The Awakening"
		/>
	</form>

	<svelte:fragment slot="footer">
		<Button on:click={() => (createModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={creating} loading={creating} on:click={createChapter}>
			Create & Open
		</Button>
	</svelte:fragment>
</Modal>

<!-- EDIT BOOK MODAL -->
<Modal open={editBookModalOpen} title="Edit Book Details" size="sm" on:close={() => (editBookModalOpen = false)}>
	{#if book}
		<form class="flex flex-col gap-4" on:submit|preventDefault={updateBook}>
			<TextField
				bind:value={editBookTitle}
				label="Book Title (Source Language)"
				placeholder="e.g. 星尘"
			/>

			<TextField
				bind:value={editBookTitleTarget}
				label="Target Title (Translated title)"
				placeholder="e.g. Stardust"
			/>

			<div class="grid grid-cols-2 gap-3">
				<div>
					<span class="mb-1 block text-xs font-semibold opacity-60">Source Language</span>
					<LanguagePicker bind:value={editBookSourceLang} />
				</div>

				<div>
					<span class="mb-1 block text-xs font-semibold opacity-60">Target Language</span>
					<LanguagePicker bind:value={editBookTargetLang} />
				</div>
			</div>

			<div class="flex flex-col gap-3 rounded-xl border border-black/10 bg-black/[0.02] p-3 dark:border-white/10 dark:bg-white/[0.02]">
				<Toggle bind:checked={editBookPinned} label="Pin series to top" />
				<Toggle bind:checked={editBookArchived} label="Archive series" />
			</div>
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<Button on:click={() => (editBookModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={updatingBook || !editBookTitle.trim()} loading={updatingBook} on:click={updateBook}>
			Save Changes
		</Button>
	</svelte:fragment>
</Modal>

<!-- EDIT CHAPTER MODAL -->
<Modal open={editChapterModalOpen} title="Edit Chapter Details" size="sm" on:close={() => (editChapterModalOpen = false)}>
	{#if editingChapter}
		<form class="flex flex-col gap-4" on:submit|preventDefault={updateChapter}>
			<TextField
				bind:value={editChapterTitle}
				label="Chapter Title (Source Language)"
				placeholder="e.g. 第1话"
			/>

			<TextField
				bind:value={editChapterTitleTarget}
				label="Target Title (Translated title)"
				placeholder="e.g. Chapter 1: The Awakening"
			/>

			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Chapter Sequence # (1-indexed)</span>
				<input
					type="number"
					min="1"
					bind:value={editChapterSeq}
					class="w-full rounded-xl border border-black/10 bg-transparent px-3 py-2 text-sm outline-none transition placeholder:opacity-40 focus:border-[#b23a2e] dark:border-white/10"
				/>
			</div>
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<Button on:click={() => (editChapterModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={updatingChapter} loading={updatingChapter} on:click={updateChapter}>
			Save Changes
		</Button>
	</svelte:fragment>
</Modal>

<!-- DELETE CONFIRMATION DIALOG -->
<ConfirmDialog
	open={deleteConfirmOpen}
	title="Delete Chapter?"
	message={`Are you sure you want to delete "${chapterToDelete?.title || 'Chapter'}"? All uploaded page images and translation output for this chapter will be permanently removed.`}
	confirmLabel="Delete Chapter"
	variant="danger"
	on:confirm={confirmDeleteChapter}
	on:cancel={() => (deleteConfirmOpen = false)}
/>
