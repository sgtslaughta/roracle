# Library and search

The Library is the start page. With nothing typed it lists the newest papers you can see. Type in the search box to search the text of papers.

<!-- screenshot: library-results: Library search results with passages, tags, votes and the filter rail -->

## Searching

- Type words, not a special syntax. There are no operators: quotes, `AND`, `OR` and field prefixes are not interpreted, and the text is matched as plain words. A query can be up to 1000 characters.
- rOracle combines keyword matching with meaning-based matching, so a paper can match without containing your exact words. Matches in the title count double against matches in the body; headings and the surrounding context of a passage also count.
- Searching starts after three characters and 300 ms of no typing. An empty box goes back to the newest-first list.
- Results are papers, each with its best passages. Matches are marked in the passage text. Click a passage to open the paper at that page with the passage highlighted.
- Only the latest version of each paper is searched.
- You only ever see papers you may see; a paper you cannot access is simply absent. See [collections and sharing](collections-sharing.md#who-can-see-a-document).
- If nothing matches you see "Nothing you can see matches …". Use fewer words or clear the filters.

Library search improvements are coming in 0.2.x.

### What a result card shows

- Title, authors, venue and year.
- The sensitivity label badge ([labels](labels.md)), the file type, the version (`v2`), and "Cited by N here" when N papers in the library cite it.
- Tags. Click a tag to filter by it.
- Up to two community notes on the paper ([notes](notes-annotations.md#community-notes)), shown as plain text. Click one to open the paper on its Notes tab.
- The three-dot menu: **Open**, **Add to collection**, **Copy title**, and **Remove from collection** when you are viewing exactly one of your collections.
- The vote control on the left (see [votes](#votes)).

### Browsing and the 200 limit

The list never shows more than 200 papers in total. If the count reads "200+", narrow the search with a query or filters. Results are 20 to a page.

## Sorting

The **Sort** menu offers **Relevance** (the default), **Newest**, **Most useful** (by votes) and **Title**. With no query, relevance is the same as newest first.

## Filters

Filters appear as a rail beside the results, or behind the **Filters** button when the window is narrow. They are built from the current results, not from a fixed list.

| Filter | Values |
|---|---|
| Collection | Your own collections ([collections](collections-sharing.md)) |
| Tags | Tags on the matching papers |
| File type | For example PDF |
| Org unit | The org unit the paper was uploaded under |
| Year | Publication year |
| Author | Authors |
| Sensitivity | Labels, ordered from least to most sensitive |

How they combine:

- Several values in one filter are OR: tags `a` and `b` shows papers with either.
- Different filters are AND: a tag and a year shows papers matching both.
- Each filter's counts ignore its own selection but respect the others, so you can see what choosing another value would give. A value you have selected stays listed even at zero.
- The rail lists up to eight values per filter; **Show all** opens the rest and a search box inside it. The server returns at most 50 values per filter.
- With no query and more than 200 papers, the note "Counts cover the 200 newest documents" reminds you the counts are approximate.

Selected filters appear as chips above the results. Click a chip to remove it, or use **Clear all**. Changing a filter returns you to page 1. The address in your browser holds the query, filters, sort and page, so you can bookmark or share a search.

<!-- screenshot: library-filters: the filter rail with a collection, a tag and a year selected, and the active-filter chips -->

## Votes

Each paper has up and down arrows with a score (up minus down).

- Click the up arrow to mark a paper useful. Click it again to take the vote back.
- Click the down arrow to say it is not useful. A menu asks why; the owner sees your reason. The choices are **Outdated**, **Wrong or misleading**, **Low quality** and **Superseded by a newer document**. Choosing the last one asks which paper replaces it, from the other papers on the same results page; it is not offered where there are none, such as in the reader.
- Votes nudge ranking: a well-liked paper ranks a little higher and a disliked one a little lower (the effect is at most 15 percent).

<!-- screenshot: vote-reasons: the downvote reason menu open on a result card -->

## Related

- [Reading a paper](reader.md)
- [Collections and sharing](collections-sharing.md)
