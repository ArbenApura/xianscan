<script lang="ts">
	// IMPORTED DEP-COMPONENTS
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button, TextField, Modal, ConfirmDialog, ActionMenu, LanguagePicker, Toggle, LazyImage, Badge } from '$lib/components/ui';
	import { ripple } from '$lib/actions/ripple';
	import { settings } from '$lib/stores/settings';
	// IMPORTED ICONS
	import BookOpen from 'lucide-svelte/icons/book-open';
	import Plus from 'lucide-svelte/icons/plus';
	import Search from 'lucide-svelte/icons/search';
	import Trash2 from 'lucide-svelte/icons/trash-2';
	import ExternalLink from 'lucide-svelte/icons/external-link';
	import Layers from 'lucide-svelte/icons/layers';
	import Pencil from 'lucide-svelte/icons/pencil';
	import Pin from 'lucide-svelte/icons/pin';
	import Archive from 'lucide-svelte/icons/archive';
	import Play from 'lucide-svelte/icons/play';
	import CheckCircle2 from 'lucide-svelte/icons/check-circle-2';
	import LayoutGrid from 'lucide-svelte/icons/layout-grid';
	import List from 'lucide-svelte/icons/list';
	import AlignJustify from 'lucide-svelte/icons/align-justify';

	// -- TYPES -- //

	interface LatestChapter {
		id: number;
		seq: number;
		title: string | null;
		titleTarget?: string | null;
		status: string;
	}

	interface Book {
		id: string;
		title: string;
		titleTarget?: string | null;
		sourceLang: string;
		targetLang: string;
		pinned?: boolean;
		archived?: boolean;
		chapterCount: number;
		translatedChapterCount?: number;
		pageCount?: number;
		translatedPageCount?: number;
		coverPageId?: number | null;
		coverHasOutput?: boolean;
		latestChapter?: LatestChapter | null;
		updatedAt?: number;
		createdAt?: number;
	}

	// -- STATES -- //

	let books: Book[] = [];
	let loading = true;
	let title = '';
	let titleTarget = '';
	let sourceLang = $settings.sourceLang;
	let targetLang = $settings.targetLang;
	let creating = false;
	let searchQuery = '';
	let createModalOpen = false;
	let activeTab: 'all' | 'active' | 'pinned' | 'archived' = 'active';

	// VIEW LAYOUT MODES: 'grid' (Comfortable Cards) | 'list' (Media List Rows) | 'compact' (Dense Table Rows)
	let viewLayout: 'grid' | 'list' | 'compact' = 'grid';

	// EDIT BOOK STATES
	let editModalOpen = false;
	let editingBook: Book | null = null;
	let editTitle = '';
	let editTitleTarget = '';
	let editSourceLang = '';
	let editTargetLang = '';
	let editPinned = false;
	let editArchived = false;
	let updating = false;

	// DELETION CONFIRMATION
	let bookToDelete: Book | null = null;
	let deleteConfirmOpen = false;
	let deleting = false;

	// -- LIFECYCLES -- //

	onMount(() => {
		try {
			const saved = localStorage.getItem('manhua:libraryViewLayout');
			if (saved === 'grid' || saved === 'list' || saved === 'compact') {
				viewLayout = saved;
			}
		} catch {
			// ignore
		}
		loadBooks();
	});

	function setViewLayout(mode: 'grid' | 'list' | 'compact') {
		viewLayout = mode;
		try {
			localStorage.setItem('manhua:libraryViewLayout', mode);
		} catch {
			// ignore
		}
	}

	// -- FUNCTIONS -- //

	async function loadBooks() {
		try {
			const resp = await fetch('/api/books');
			books = (await resp.json()).books;
		} catch {
			toast.error('Could not load books.');
		} finally {
			loading = false;
		}
	}

	async function createBook() {
		const t = title.trim();
		if (!t) return;
		creating = true;
		try {
			const resp = await fetch('/api/books', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					title: t,
					titleTarget: titleTarget.trim() || undefined,
					sourceLang,
					targetLang,
				}),
			});
			if (!resp.ok) throw new Error('create failed');
			const { id } = await resp.json();
			toast.success('Book created.');
			createModalOpen = false;
			title = '';
			titleTarget = '';
			goto(`/app/books/${id}/`);
		} catch {
			toast.error('Could not create the book.');
		} finally {
			creating = false;
		}
	}

	function openEditBook(book: Book) {
		editingBook = book;
		editTitle = book.title;
		editTitleTarget = book.titleTarget || '';
		editSourceLang = book.sourceLang;
		editTargetLang = book.targetLang;
		editPinned = !!book.pinned;
		editArchived = !!book.archived;
		editModalOpen = true;
	}

	async function updateBook() {
		if (!editingBook) return;
		const t = editTitle.trim();
		if (!t) return;
		updating = true;
		try {
			const resp = await fetch(`/api/books/${editingBook.id}`, {
				method: 'PATCH',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					title: t,
					titleTarget: editTitleTarget.trim() || null,
					sourceLang: editSourceLang,
					targetLang: editTargetLang,
					pinned: editPinned,
					archived: editArchived,
				}),
			});
			if (!resp.ok) throw new Error('Update failed');
			const data = await resp.json();
			const updated = data.book;
			books = books.map((b) => (b.id === updated.id ? { ...b, ...updated } : b));
			toast.success('Book updated.');
			editModalOpen = false;
			editingBook = null;
		} catch {
			toast.error('Could not update the book.');
		} finally {
			updating = false;
		}
	}

	async function togglePin(book: Book) {
		try {
			const newPinned = !book.pinned;
			const resp = await fetch(`/api/books/${book.id}`, {
				method: 'PATCH',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ pinned: newPinned }),
			});
			if (!resp.ok) throw new Error('Pin failed');
			const data = await resp.json();
			books = books.map((b) => (b.id === book.id ? { ...b, ...data.book } : b));
			toast.success(newPinned ? `Pinned "${book.title}".` : `Unpinned "${book.title}".`);
		} catch {
			toast.error('Could not change pin status.');
		}
	}

	async function toggleArchive(book: Book) {
		try {
			const newArchived = !book.archived;
			const resp = await fetch(`/api/books/${book.id}`, {
				method: 'PATCH',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ archived: newArchived }),
			});
			if (!resp.ok) throw new Error('Archive failed');
			const data = await resp.json();
			books = books.map((b) => (b.id === book.id ? { ...b, ...data.book } : b));
			toast.success(newArchived ? `Archived "${book.title}".` : `Unarchived "${book.title}".`);
		} catch {
			toast.error('Could not change archive status.');
		}
	}

	function promptDeleteBook(book: Book) {
		bookToDelete = book;
		deleteConfirmOpen = true;
	}

	async function confirmDeleteBook() {
		if (!bookToDelete) return;
		deleting = true;
		try {
			const resp = await fetch(`/api/books/${bookToDelete.id}`, {
				method: 'DELETE',
			});
			if (!resp.ok) throw new Error('Delete failed');
			toast.success(`Deleted "${bookToDelete.title}".`);
			books = books.filter((b) => b.id !== bookToDelete?.id);
		} catch {
			toast.error('Could not delete the book.');
		} finally {
			deleting = false;
			deleteConfirmOpen = false;
			bookToDelete = null;
		}
	}

	function timeAgo(epoch?: number): string {
		if (!epoch) return 'Recently';
		const diff = Date.now() - epoch;
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'Just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		if (days < 30) return `${days}d ago`;
		return new Date(epoch).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}

	function getProgress(book: Book): { percent: number; label: string; isComplete: boolean } {
		const totalCh = book.chapterCount || 0;
		const doneCh = book.translatedChapterCount || 0;
		const totalPages = book.pageCount || 0;
		const donePages = book.translatedPageCount || 0;

		if (totalCh === 0) return { percent: 0, label: '0 chapters', isComplete: false };

		const percent =
			totalPages > 0
				? Math.min(100, Math.round((donePages / totalPages) * 100))
				: Math.min(100, Math.round((doneCh / totalCh) * 100));

		const isComplete = (totalCh > 0 && doneCh === totalCh) || (totalPages > 0 && donePages === totalPages);

		let label = '';
		if (isComplete) {
			label = '100% Complete';
		} else if (totalPages > 0) {
			label = `${doneCh}/${totalCh} chs (${donePages}/${totalPages} pgs · ${percent}%)`;
		} else {
			label = `${doneCh}/${totalCh} chs (${percent}%)`;
		}

		return {
			percent,
			label,
			isComplete,
		};
	}

	// REACTIVE FILTERED & SORTED BOOKS (PINNED FLOATS TO TOP)
	$: filteredBooks = books
		.filter((b) => {
			// TAB FILTER
			if (activeTab === 'active' && b.archived) return false;
			if (activeTab === 'pinned' && (!b.pinned || b.archived)) return false;
			if (activeTab === 'archived' && !b.archived) return false;

			// SEARCH FILTER
			if (!searchQuery.trim()) return true;
			const q = searchQuery.toLowerCase();
			return (
				b.title.toLowerCase().includes(q) ||
				(b.titleTarget && b.titleTarget.toLowerCase().includes(q)) ||
				b.sourceLang.toLowerCase().includes(q) ||
				b.targetLang.toLowerCase().includes(q)
			);
		})
		.sort((a, b) => {
			const pinA = a.pinned ? 1 : 0;
			const pinB = b.pinned ? 1 : 0;
			if (pinA !== pinB) return pinB - pinA;
			return (b.updatedAt ?? 0) - (a.updatedAt ?? 0);
		});

	$: totalChapters = books.reduce((sum, b) => sum + (b.chapterCount || 0), 0);
	$: pinnedCount = books.filter((b) => b.pinned && !b.archived).length;
	$: archivedCount = books.filter((b) => b.archived).length;
</script>

<svelte:head>
	<title>Library — Manhua Translator</title>
</svelte:head>

<!-- LIBRARY DASHBOARD -->
<div class="flex flex-col gap-6">
	<!-- HEADER SECTION -->
	<div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
		<div>
			<h1 class="text-2xl font-bold tracking-tight sm:text-3xl">Library</h1>
			<p class="mt-1 text-sm opacity-60">Manage series, track translation progress, and read translated chapters.</p>
		</div>

		<div class="flex items-center gap-3">
			<Button variant="primary" on:click={() => (createModalOpen = true)}>
				<Plus size={16} /> New Book
			</Button>
		</div>
	</div>

	<!-- STATS SUMMARY BAR -->
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
		<div class="rounded-2xl border border-black/[0.06] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="flex items-center gap-2 text-xs font-semibold opacity-60">
				<BookOpen size={14} class="text-[#b23a2e] dark:text-[#e08a63]" /> Total Series
			</div>
			<div class="mt-1 text-2xl font-bold">{books.length}</div>
		</div>

		<div class="rounded-2xl border border-black/[0.06] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03]">
			<div class="flex items-center gap-2 text-xs font-semibold opacity-60">
				<Layers size={14} class="text-amber-600 dark:text-amber-400" /> Total Chapters
			</div>
			<div class="mt-1 text-2xl font-bold">{totalChapters}</div>
		</div>

		<div class="col-span-2 rounded-2xl border border-black/[0.06] bg-white/50 p-4 backdrop-blur dark:border-white/[0.06] dark:bg-white/[0.03] sm:col-span-1">
			<div class="flex items-center justify-between text-xs font-semibold opacity-60">
				<span>Global Glossary</span>
				<a href="/app/glossary/" class="text-[#b23a2e] hover:underline dark:text-[#e08a63]">Manage →</a>
			</div>
			<div class="mt-1 text-xs opacity-70">Enforces consistent character & term translations</div>
		</div>
	</div>

	<!-- SHELF TABS & SEARCH TOOLBAR WITH VIEW MODES -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<!-- TABS -->
		<div class="flex items-center gap-1 rounded-xl bg-black/[0.04] p-1 dark:bg-white/[0.04]">
			<button
				type="button"
				on:click={() => (activeTab = 'active')}
				class={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
					activeTab === 'active'
						? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
						: 'opacity-60 hover:opacity-100'
				}`}
				use:ripple
			>
				Active ({books.filter((b) => !b.archived).length})
			</button>

			<button
				type="button"
				on:click={() => (activeTab = 'pinned')}
				class={`flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
					activeTab === 'pinned'
						? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
						: 'opacity-60 hover:opacity-100'
				}`}
				use:ripple
			>
				<Pin size={12} class="rotate-45" />
				<span>Pinned ({pinnedCount})</span>
			</button>

			<button
				type="button"
				on:click={() => (activeTab = 'archived')}
				class={`flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
					activeTab === 'archived'
						? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
						: 'opacity-60 hover:opacity-100'
				}`}
				use:ripple
			>
				<Archive size={12} />
				<span>Archived ({archivedCount})</span>
			</button>

			<button
				type="button"
				on:click={() => (activeTab = 'all')}
				class={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
					activeTab === 'all'
						? 'bg-white text-black shadow-xs dark:bg-[#201c18] dark:text-white'
						: 'opacity-60 hover:opacity-100'
				}`}
				use:ripple
			>
				All ({books.length})
			</button>
		</div>

		<!-- CONTROLS: VIEW SWITCHER & SEARCH -->
		<div class="flex items-center gap-2">
			<!-- VIEW SWITCHER SEGMENTED TABS -->
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

			<!-- SEARCH INPUT -->
			<div class="relative min-w-0 flex-1 max-w-xs">
				<Search size={14} class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 opacity-40" />
				<input
					bind:value={searchQuery}
					type="search"
					placeholder="Search books..."
					class="w-full rounded-xl border border-black/10 bg-transparent py-1.5 pl-8 pr-3 text-xs sm:text-sm outline-none transition placeholder:opacity-40 focus:border-[#b23a2e] dark:border-white/10"
				/>
			</div>
		</div>
	</div>

	<!-- BOOK LISTINGS -->
	{#if loading}
		<div class="grid w-full gap-5 sm:grid-cols-2">
			{#each [1, 2, 3, 4] as _}
				<div class="h-48 animate-pulse rounded-2xl border border-black/[0.06] bg-black/[0.03] dark:border-white/[0.06] dark:bg-white/[0.03]"></div>
			{/each}
		</div>
	{:else if books.length === 0}
		<div class="flex flex-col items-center justify-center rounded-2xl border border-dashed border-black/15 py-16 text-center dark:border-white/15">
			<div class="flex h-12 w-12 items-center justify-center rounded-full bg-[#b23a2e]/10 text-[#b23a2e] dark:text-[#e08a63]">
				<BookOpen size={24} />
			</div>
			<h2 class="mt-4 text-base font-semibold">No books in your library</h2>
			<p class="mt-1 max-w-sm text-xs opacity-60">Create your first book series to start uploading chapter images for translation.</p>
			<Button variant="primary" size="sm" class="mt-4" on:click={() => (createModalOpen = true)}>
				<Plus size={14} /> Create First Book
			</Button>
		</div>
	{:else if filteredBooks.length === 0}
		<p class="py-8 text-center text-sm opacity-60">No books found matching "{searchQuery}".</p>
	{:else if viewLayout === 'grid'}
		<!-- MODE 1: COMFORTABLE 2-COLUMN CARDS GRID -->
		<ul class="grid w-full gap-5 sm:grid-cols-2">
			{#each filteredBooks as book (book.id)}
				{@const progress = getProgress(book)}
				<li class="group relative flex flex-col justify-between rounded-2xl border border-black/[0.08] bg-white/60 p-4 transition-all duration-300 hover:border-[#b23a2e]/40 hover:shadow-xl dark:border-white/[0.06] dark:bg-white/[0.02]">
					<!-- UPPER SECTION: COVER ARTWORK + METADATA -->
					<div class="flex gap-4">
						<!-- 2:3 VERTICAL COVER THUMBNAIL WITH LAZY LOADING & SKELETON SHIMMER -->
						<a
							href={`/app/books/${book.id}/`}
							class="group/cover w-24 sm:w-28 shrink-0 transition-transform duration-300 hover:scale-102"
							title={`Open ${book.title}`}
						>
							<LazyImage
								src={book.coverPageId ? `/api/pages/${book.coverPageId}/file?kind=thumb&w=320` : ''}
								alt={`${book.title} Cover`}
								fallbackText={book.title.slice(0, 1) || '书'}
								aspectRatio="aspect-[2/3]"
								showSpineShadow={true}
							/>
						</a>

						<!-- METADATA DETAILS -->
						<div class="min-w-0 flex-1 flex flex-col justify-between">
							<div>
								<div class="flex items-start justify-between gap-1.5">
									<div class="min-w-0 flex-1">
										<div class="flex items-center gap-1.5 flex-wrap">
											{#if book.pinned}
												<span title="Pinned Series" class="flex items-center text-amber-600 dark:text-amber-400">
													<Pin size={12} class="rotate-45 fill-current" />
												</span>
											{/if}
											<a
												href={`/app/books/${book.id}/`}
												class="font-bold text-base tracking-tight hover:text-[#b23a2e] dark:hover:text-[#e08a63] block truncate"
												title={book.title}
											>
												{book.title}
											</a>
										</div>
										{#if book.titleTarget}
											<p class="text-xs opacity-60 font-medium truncate mt-0.5" title={book.titleTarget}>
												{book.titleTarget}
											</p>
										{/if}
									</div>

									<ActionMenu
										items={[
											{ value: 'open', label: 'Open Series', icon: ExternalLink },
											{ value: 'edit', label: 'Edit Book Details', icon: Pencil },
											{ value: 'pin', label: book.pinned ? 'Unpin from Top' : 'Pin to Top', icon: Pin },
											{ value: 'archive', label: book.archived ? 'Unarchive Series' : 'Archive Series', icon: Archive },
											{ value: 'delete', label: 'Delete Book', icon: Trash2, danger: true },
										]}
										on:select={(e) => {
											if (e.detail === 'open') goto(`/app/books/${book.id}/`);
											else if (e.detail === 'edit') openEditBook(book);
											else if (e.detail === 'pin') togglePin(book);
											else if (e.detail === 'archive') toggleArchive(book);
											else if (e.detail === 'delete') promptDeleteBook(book);
										}}
									/>
								</div>

								<!-- LANGUAGE & VOLUME PILLS -->
								<div class="mt-2.5 flex flex-wrap items-center gap-1.5 text-[11px]">
									<span class="rounded-md bg-[#b23a2e]/10 px-2 py-0.5 font-semibold text-[#b23a2e] dark:text-[#e08a63]">
										{book.sourceLang} → {book.targetLang}
									</span>
									<span class="rounded-md bg-black/5 dark:bg-white/5 px-2 py-0.5 font-medium opacity-70">
										{book.chapterCount} {book.chapterCount === 1 ? 'ch' : 'chs'}
									</span>
								</div>
							</div>

							<!-- LIVE TRANSLATION PROGRESS BAR -->
							<div class="mt-3">
								<div class="flex items-center justify-between text-[11px] mb-1">
									<span class="opacity-60 flex items-center gap-1 font-medium">
										{#if progress.isComplete}
											<CheckCircle2 size={11} class="text-emerald-600 dark:text-emerald-400" />
										{/if}
										{progress.label}
									</span>
									<span class="opacity-40 text-[10px] font-mono">{timeAgo(book.updatedAt)}</span>
								</div>
								<div class="h-1.5 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
									<div
										class={`h-full rounded-full transition-all duration-500 ${
											progress.isComplete
												? 'bg-emerald-600 dark:bg-emerald-400'
												: 'bg-[#b23a2e] dark:bg-[#e08a63]'
										}`}
										style={`width: ${progress.percent}%`}
									></div>
								</div>
							</div>
						</div>
					</div>

					<!-- LOWER SECTION: ACTION FOOTER BAR -->
					<div class="mt-4 flex items-center justify-between border-t border-black/[0.05] pt-3 text-xs dark:border-white/[0.05]">
						{#if book.latestChapter}
							<a
								href={`/app/books/${book.id}/chapters/${book.latestChapter.id}/`}
								class="inline-flex items-center gap-1.5 rounded-lg bg-[#b23a2e]/10 px-2.5 py-1 font-semibold text-[#b23a2e] transition hover:bg-[#b23a2e] hover:text-white dark:text-[#e08a63] dark:hover:bg-[#e08a63] dark:hover:text-black"
								use:ripple
							>
								<Play size={11} class="fill-current" />
								<span>{book.latestChapter.title || `Ch. ${book.latestChapter.seq + 1}`}</span>
							</a>
						{:else}
							<span class="text-[11px] opacity-40">No chapters yet</span>
						{/if}

						<a
							href={`/app/books/${book.id}/`}
							class="font-medium opacity-70 transition hover:opacity-100 hover:text-[#b23a2e] dark:hover:text-[#e08a63]"
						>
							Manage Series →
						</a>
					</div>
				</li>
			{/each}
		</ul>
	{:else if viewLayout === 'list'}
		<!-- MODE 2: MEDIA LIST STRIP (HORIZONTAL ROWS) -->
		<ul class="flex flex-col gap-3 w-full">
			{#each filteredBooks as book (book.id)}
				{@const progress = getProgress(book)}
				<li class="group flex items-center justify-between gap-4 rounded-xl border border-black/[0.07] bg-white/60 p-3.5 transition-all hover:border-[#b23a2e]/40 hover:bg-white hover:shadow-md dark:border-white/[0.06] dark:bg-white/[0.02] dark:hover:bg-white/[0.04]">
					<div class="flex items-center gap-3.5 min-w-0 flex-1">
						<!-- 48px MINI THUMBNAIL -->
						<a
							href={`/app/books/${book.id}/`}
							class="w-12 sm:w-14 shrink-0 transition-transform duration-200 group-hover:scale-105"
							title={`Open ${book.title}`}
						>
							<LazyImage
								src={book.coverPageId ? `/api/pages/${book.coverPageId}/file?kind=thumb&w=160` : ''}
								alt={`${book.title} Cover`}
								fallbackText={book.title.slice(0, 1) || '书'}
								aspectRatio="aspect-[2/3]"
								showSpineShadow={false}
								class="rounded-lg shadow-2xs"
							/>
						</a>

						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2 flex-wrap">
								{#if book.pinned}
									<span title="Pinned Series" class="text-amber-600 dark:text-amber-400">
										<Pin size={13} class="rotate-45 fill-current" />
									</span>
								{/if}
								<a
									href={`/app/books/${book.id}/`}
									class="font-bold text-sm sm:text-base hover:text-[#b23a2e] dark:hover:text-[#e08a63] truncate"
									title={book.title}
								>
									{book.title}
								</a>
								{#if book.titleTarget}
									<span class="text-xs opacity-60 font-medium truncate hidden sm:inline" title={book.titleTarget}>
										({book.titleTarget})
									</span>
								{/if}
								<span class="rounded-md bg-[#b23a2e]/10 px-2 py-0.5 text-[10px] font-semibold text-[#b23a2e] dark:text-[#e08a63]">
									{book.sourceLang} → {book.targetLang}
								</span>
							</div>

							<div class="mt-1 flex items-center gap-3 text-xs opacity-65">
								<span>{book.chapterCount} chapters ({book.pageCount || 0} pgs)</span>
								<span>•</span>
								<span class={progress.isComplete ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}>
									{progress.label}
								</span>
								<span class="hidden sm:inline opacity-40">• {timeAgo(book.updatedAt)}</span>
							</div>
						</div>
					</div>

					<div class="flex items-center gap-2.5 shrink-0">
						{#if book.latestChapter}
							<a
								href={`/app/books/${book.id}/chapters/${book.latestChapter.id}/`}
								class="hidden sm:inline-flex items-center gap-1 rounded-lg bg-[#b23a2e]/10 px-2.5 py-1 text-xs font-semibold text-[#b23a2e] transition hover:bg-[#b23a2e] hover:text-white dark:text-[#e08a63] dark:hover:bg-[#e08a63] dark:hover:text-black"
								use:ripple
							>
								<Play size={11} class="fill-current" />
								<span>{book.latestChapter.title || `Ch. ${book.latestChapter.seq + 1}`}</span>
							</a>
						{/if}

						<a
							href={`/app/books/${book.id}/`}
							class="rounded-lg border border-black/10 px-2.5 py-1 text-xs font-medium opacity-80 transition hover:opacity-100 hover:border-black/25 dark:border-white/10 dark:hover:border-white/25"
						>
							Manage →
						</a>

						<ActionMenu
							items={[
								{ value: 'open', label: 'Open Series', icon: ExternalLink },
								{ value: 'edit', label: 'Edit Book Details', icon: Pencil },
								{ value: 'pin', label: book.pinned ? 'Unpin from Top' : 'Pin to Top', icon: Pin },
								{ value: 'archive', label: book.archived ? 'Unarchive Series' : 'Archive Series', icon: Archive },
								{ value: 'delete', label: 'Delete Book', icon: Trash2, danger: true },
							]}
							on:select={(e) => {
								if (e.detail === 'open') goto(`/app/books/${book.id}/`);
								else if (e.detail === 'edit') openEditBook(book);
								else if (e.detail === 'pin') togglePin(book);
								else if (e.detail === 'archive') toggleArchive(book);
								else if (e.detail === 'delete') promptDeleteBook(book);
							}}
						/>
					</div>
				</li>
			{/each}
		</ul>
	{:else}
		<!-- MODE 3: DENSE TABLE / COMPACT ROWS (FOR POWER BROWSING) -->
		<div class="overflow-hidden rounded-xl border border-black/[0.08] bg-white/60 shadow-xs dark:border-white/[0.06] dark:bg-white/[0.02]">
			<table class="w-full text-left text-xs border-collapse">
				<thead>
					<tr class="border-b border-black/[0.06] bg-black/[0.02] text-[11px] font-semibold opacity-60 dark:border-white/[0.06] dark:bg-white/[0.02]">
						<th class="py-2.5 pl-4 pr-2 w-10">★</th>
						<th class="py-2.5 px-3">Book Series Title</th>
						<th class="py-2.5 px-3 hidden md:table-cell">Translated Subtitle</th>
						<th class="py-2.5 px-3 w-28">Languages</th>
						<th class="py-2.5 px-3 w-24">Chapters</th>
						<th class="py-2.5 px-3 w-36">Progress</th>
						<th class="py-2.5 pr-4 pl-3 w-24 text-right">Actions</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-black/[0.04] dark:divide-white/[0.04]">
					{#each filteredBooks as book (book.id)}
						{@const progress = getProgress(book)}
						<tr class="group transition hover:bg-black/[0.02] dark:hover:bg-white/[0.02]">
							<td class="py-2.5 pl-4 pr-2">
								{#if book.pinned}
									<span title="Pinned Series" class="text-amber-600 dark:text-amber-400">
										<Pin size={12} class="rotate-45 fill-current" />
									</span>
								{:else}
									<span class="opacity-20">•</span>
								{/if}
							</td>
							<td class="py-2.5 px-3 font-semibold">
								<a
									href={`/app/books/${book.id}/`}
									class="hover:text-[#b23a2e] dark:hover:text-[#e08a63]"
								>
									{book.title}
								</a>
							</td>
							<td class="py-2.5 px-3 opacity-60 hidden md:table-cell truncate max-w-xs">
								{book.titleTarget || '—'}
							</td>
							<td class="py-2.5 px-3">
								<span class="rounded bg-[#b23a2e]/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-[#b23a2e] dark:text-[#e08a63]">
									{book.sourceLang} → {book.targetLang}
								</span>
							</td>
							<td class="py-2.5 px-3 font-mono opacity-70">
								{book.chapterCount} chs ({book.pageCount || 0} pgs)
							</td>
							<td class="py-2.5 px-3">
								<div class="flex items-center gap-2">
									<div class="h-1.5 w-16 overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
										<div
											class={`h-full rounded-full ${
												progress.isComplete ? 'bg-emerald-600 dark:bg-emerald-400' : 'bg-[#b23a2e] dark:bg-[#e08a63]'
											}`}
											style={`width: ${progress.percent}%`}
										></div>
									</div>
									<span class="font-mono text-[10px] opacity-60">{progress.percent}%</span>
								</div>
							</td>
							<td class="py-2.5 pr-4 pl-3 text-right">
								<div class="flex items-center justify-end gap-1.5">
									<a
										href={`/app/books/${book.id}/`}
										class="p-1 rounded opacity-70 hover:opacity-100 hover:text-[#b23a2e]"
										title="Open Series"
									>
										<ExternalLink size={13} />
									</a>
									<ActionMenu
										items={[
											{ value: 'open', label: 'Open Series', icon: ExternalLink },
											{ value: 'edit', label: 'Edit Book Details', icon: Pencil },
											{ value: 'pin', label: book.pinned ? 'Unpin from Top' : 'Pin to Top', icon: Pin },
											{ value: 'archive', label: book.archived ? 'Unarchive Series' : 'Archive Series', icon: Archive },
											{ value: 'delete', label: 'Delete Book', icon: Trash2, danger: true },
										]}
										on:select={(e) => {
											if (e.detail === 'open') goto(`/app/books/${book.id}/`);
											else if (e.detail === 'edit') openEditBook(book);
											else if (e.detail === 'pin') togglePin(book);
											else if (e.detail === 'archive') toggleArchive(book);
											else if (e.detail === 'delete') promptDeleteBook(book);
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
</div>

<!-- CREATE BOOK MODAL -->
<Modal open={createModalOpen} title="Create New Book Series" size="sm" on:close={() => (createModalOpen = false)}>
	<form class="flex flex-col gap-4" on:submit|preventDefault={createBook}>
		<TextField
			bind:value={title}
			label="Book Title (Source Language)"
			placeholder="e.g. 星尘"
		/>

		<TextField
			bind:value={titleTarget}
			label="Target Title (Optional translation)"
			placeholder="e.g. Stardust"
		/>

		<div class="grid grid-cols-2 gap-3">
			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Source Language</span>
				<LanguagePicker bind:value={sourceLang} />
			</div>

			<div>
				<span class="mb-1 block text-xs font-semibold opacity-60">Target Language</span>
				<LanguagePicker bind:value={targetLang} />
			</div>
		</div>
	</form>

	<svelte:fragment slot="footer">
		<Button on:click={() => (createModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={creating || !title.trim()} loading={creating} on:click={createBook}>
			Create Book
		</Button>
	</svelte:fragment>
</Modal>

<!-- EDIT BOOK MODAL -->
<Modal open={editModalOpen} title="Edit Book Series" size="sm" on:close={() => (editModalOpen = false)}>
	{#if editingBook}
		<form class="flex flex-col gap-4" on:submit|preventDefault={updateBook}>
			<TextField
				bind:value={editTitle}
				label="Book Title (Source Language)"
				placeholder="e.g. 星尘"
			/>

			<TextField
				bind:value={editTitleTarget}
				label="Target Title (Translated title)"
				placeholder="e.g. Stardust"
			/>

			<div class="grid grid-cols-2 gap-3">
				<div>
					<span class="mb-1 block text-xs font-semibold opacity-60">Source Language</span>
					<LanguagePicker bind:value={editSourceLang} />
				</div>

				<div>
					<span class="mb-1 block text-xs font-semibold opacity-60">Target Language</span>
					<LanguagePicker bind:value={editTargetLang} />
				</div>
			</div>

			<div class="flex flex-col gap-3 rounded-xl border border-black/10 bg-black/[0.02] p-3 dark:border-white/10 dark:bg-white/[0.02]">
				<Toggle bind:checked={editPinned} label="Pin series to top of library" />
				<Toggle bind:checked={editArchived} label="Archive series (hide from active view)" />
			</div>
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<Button on:click={() => (editModalOpen = false)}>Cancel</Button>
		<Button variant="primary" disabled={updating || !editTitle.trim()} loading={updating} on:click={updateBook}>
			Save Changes
		</Button>
	</svelte:fragment>
</Modal>

<!-- DELETE CONFIRMATION DIALOG -->
<ConfirmDialog
	open={deleteConfirmOpen}
	title="Delete Book Series?"
	message={`Are you sure you want to delete "${bookToDelete?.title}"? All chapters, pages, and cached translations for this book will be permanently deleted.`}
	confirmLabel="Delete Book"
	variant="danger"
	on:confirm={confirmDeleteBook}
	on:cancel={() => (deleteConfirmOpen = false)}
/>
