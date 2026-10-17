# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Tests for mandatory eBay description link sanitation (fully offline)."""

from kleinanzeigen_bot.ebay_description import sanitize_description


def test_plain_text_without_links_is_unchanged() -> None:
    text, removed = sanitize_description("Solid oak table, pickup only.")
    assert text == "Solid oak table, pickup only."
    assert removed == 0


def test_empty_description() -> None:
    assert sanitize_description("") == ("", 0)


def test_input_string_is_never_modified() -> None:
    original = "Visit https://example.com and [shop](https://shop.example.de/a)!"
    snapshot = str(original)
    sanitize_description(original)
    assert original == snapshot


def test_https_url_is_removed() -> None:
    text, removed = sanitize_description("Visit https://example.com today.")
    assert text == "Visit today."
    assert removed == 1


def test_http_url_with_query_and_fragment_is_removed() -> None:
    text, removed = sanitize_description("See http://example.com/a?x=1&y=2#part.")
    assert text == "See ."
    assert removed == 1


def test_uppercase_scheme_and_host_are_removed() -> None:
    text, removed = sanitize_description("See HTTPS://EXAMPLE.COM/Page.")
    assert text == "See ."
    assert removed == 1


def test_ftp_url_is_removed() -> None:
    text, removed = sanitize_description("Mirror at ftp://files.example.com/pub.")
    assert text == "Mirror at ."
    assert removed == 1


def test_trailing_punctuation_is_preserved() -> None:
    text, removed = sanitize_description("One https://a.example, two https://b.example; three https://c.example!")
    assert text == "One , two ; three !"
    assert removed == 3


def test_url_in_parentheses_keeps_parentheses() -> None:
    text, removed = sanitize_description("Details (https://example.com/shop) here.")
    assert text == "Details () here."
    assert removed == 1


def test_url_in_quotes_keeps_quotes() -> None:
    text, removed = sanitize_description('He said "see https://example.com".')
    assert text == 'He said "see ".'
    assert removed == 1


def test_balanced_paren_inside_url_is_kept_with_url() -> None:
    # The whole Wikipedia-style URL is one link; nothing of it must survive.
    text, removed = sanitize_description("See https://en.wikipedia.org/wiki/PC_(band) now.")
    assert text == "See now."
    assert removed == 1


def test_www_url_is_removed() -> None:
    text, removed = sanitize_description("Shop at www.example.com/sale today.")
    assert text == "Shop at today."
    assert removed == 1


def test_bare_domains_are_removed() -> None:
    text, removed = sanitize_description("Try example.com or shop.example.de/path?q=1.")
    assert text == "Try or ."
    assert removed == 2


def test_bare_domain_with_port_is_removed() -> None:
    text, removed = sanitize_description("Status on status.example.com:8080/health.")
    assert text == "Status on ."
    assert removed == 1


def test_url_only_input_returns_empty_text() -> None:
    text, removed = sanitize_description("https://example.com")
    assert not text
    assert removed == 1


def test_email_address_is_preserved() -> None:
    text, removed = sanitize_description("Contact user@example.com please.")
    assert text == "Contact user@example.com please."
    assert removed == 0


def test_obfuscated_email_address_is_preserved() -> None:
    text, removed = sanitize_description("Contact user(at)example.com please.")
    assert text == "Contact user(at)example.com please."
    assert removed == 0


def test_version_numbers_are_preserved() -> None:
    text, removed = sanitize_description("Firmware v1.2.3 installed.")
    assert text == "Firmware v1.2.3 installed."
    assert removed == 0


def test_entity_encoded_url_is_removed() -> None:
    text, removed = sanitize_description("Visit https&#58;&#47;&#47;example&#46;com now.")
    assert text == "Visit now."
    assert removed == 1


def test_double_encoded_url_is_removed() -> None:
    text, removed = sanitize_description("Visit https&amp;#58;//example.com now.")
    assert text == "Visit now."
    assert removed == 1


def test_entities_in_plain_text_are_decoded() -> None:
    text, removed = sanitize_description("Fish &amp; chips &lt;3.")
    assert text == "Fish & chips <3."
    assert removed == 0


def test_multiline_paragraphs_are_preserved() -> None:
    text, removed = sanitize_description("First line https://example.com\nstill first.\n\nSecond paragraph.")
    assert text == "First line\nstill first.\n\nSecond paragraph."
    assert removed == 1


def test_crlf_and_excess_blank_lines_are_normalized() -> None:
    text, removed = sanitize_description("One.\r\n\r\n\r\n\r\nTwo  with   spaces.")
    assert text == "One.\n\nTwo with spaces."
    assert removed == 0


def test_markdown_inline_link_keeps_label() -> None:
    text, removed = sanitize_description("[Sale](https://example.com/shop) ends soon!")
    assert text == "Sale ends soon!"
    assert removed == 1


def test_markdown_inline_link_with_title_keeps_label() -> None:
    text, removed = sanitize_description('[Sale](https://example.com "title")!')
    assert text == "Sale!"
    assert removed == 1


def test_markdown_inline_link_with_url_label_is_dropped() -> None:
    text, removed = sanitize_description("[https://example.com](https://example.com) here.")
    assert text == "here."
    assert removed == 1


def test_markdown_inline_link_with_bare_domain_label_is_dropped() -> None:
    text, removed = sanitize_description("[example.com](https://example.com) here.")
    assert text == "here."
    assert removed == 1


def test_markdown_inline_link_with_empty_label_is_dropped() -> None:
    text, removed = sanitize_description("[](https://example.com) here.")
    assert text == "here."
    assert removed == 1


def test_markdown_image_keeps_alt_text() -> None:
    text, removed = sanitize_description("Look: ![oak table](https://example.com/t.jpg)!")
    assert text == "Look: oak table!"
    assert removed == 1


def test_markdown_multiline_link_keeps_label() -> None:
    text, removed = sanitize_description("[click\n  here](https://example.com/x)")
    assert text == "click here"
    assert removed == 1


def test_markdown_reference_link_keeps_label() -> None:
    text, removed = sanitize_description("[our shop][s] has it.\n\n[s]: https://example.com/shop")
    assert text == "our shop has it."
    assert removed == 1


def test_markdown_collapsed_reference_link_keeps_label() -> None:
    text, removed = sanitize_description("[Shop][] has it.\n\n[shop]: https://example.com")
    assert text == "Shop has it."
    assert removed == 1


def test_markdown_shortcut_reference_link_keeps_label() -> None:
    text, removed = sanitize_description("[Shop] has it.\n\n[shop]: https://example.com")
    assert text == "Shop has it."
    assert removed == 1


def test_markdown_orphan_reference_definition_is_removed_and_counted() -> None:
    text, removed = sanitize_description("Text.\n\n[shop]: https://example.com")
    assert text == "Text."
    assert removed == 1


def test_markdown_shared_reference_counts_each_usage() -> None:
    text, removed = sanitize_description("[a][s] and [b][s].\n\n[s]: https://example.com")
    assert text == "a and b."
    assert removed == 2


def test_markdown_undefined_reference_is_kept_literally() -> None:
    text, removed = sanitize_description("[a][missing] stays.")
    assert text == "[a][missing] stays."
    assert removed == 0


def test_markdown_reference_with_url_label_is_dropped() -> None:
    text, removed = sanitize_description("[example.com][s] here.\n\n[s]: https://example.com")
    assert text == "here."
    assert removed == 1


def test_markdown_autolink_is_removed() -> None:
    text, removed = sanitize_description("See <https://example.com/shop> now.")
    assert text == "See now."
    assert removed == 1


def test_embedded_url_inside_kept_label_is_removed_separately() -> None:
    text, removed = sanitize_description("[see example.com here](https://other.example)")
    assert text == "see here"
    assert removed == 2


def test_footnote_definition_is_kept_literally() -> None:
    text, removed = sanitize_description("Note[^1].\n\n[^1]: see manual page 5")
    assert text == "Note[^1].\n\n[^1]: see manual page 5"
    assert removed == 0


def test_html_anchor_keeps_meaningful_label() -> None:
    text, removed = sanitize_description('<a href="https://example.com">our shop</a> here.')
    assert text == "our shop here."
    assert removed == 1


def test_html_anchor_with_url_label_is_dropped() -> None:
    text, removed = sanitize_description('<a href="https://example.com">https://example.com</a> here.')
    assert text == "here."
    assert removed == 1


def test_html_anchor_with_bare_domain_label_is_dropped() -> None:
    text, removed = sanitize_description('<a href="https://example.com">example.com</a> here.')
    assert text == "here."
    assert removed == 1


def test_html_anchor_variants_keep_label() -> None:
    text, removed = sanitize_description(
        "<A CLASS=\"x\" HREF='https://a.example'>one</A> "
        "<a href=https://b.example>two</a> "
        '<a\nhref="https://c.example"\n>three</a>'
    )
    assert text == "one two three"
    assert removed == 3


def test_html_anchor_without_href_keeps_text_uncounted() -> None:
    text, removed = sanitize_description('<a name="top">chapter</a> here.')
    assert text == "chapter here."
    assert removed == 0


def test_html_anchor_with_nested_tags_keeps_label_text() -> None:
    text, removed = sanitize_description('<a href="https://example.com"><b>big</b> sale</a>!')
    assert text == "big sale!"
    assert removed == 1


def test_html_anchor_with_entity_encoded_target_and_label() -> None:
    text, removed = sanitize_description('<a href="https&#58;//example&#46;com">our&#32;shop</a>!')
    assert text == "our shop!"
    assert removed == 1


def test_script_and_style_content_is_dropped() -> None:
    text, removed = sanitize_description('Text<script>alert("https://evil.example")</script> and <style>a{color:red}</style>done.')
    assert text == "Text and done."
    assert removed == 0


def test_iframe_and_comments_are_dropped() -> None:
    text, removed = sanitize_description('A<!-- https://example.com -->B<iframe src="https://x.example"></iframe>C')
    assert text == "ABC"
    assert removed == 0


def test_block_tags_become_paragraph_breaks() -> None:
    text, removed = sanitize_description("<p>One</p><p>Two<br>still two</p><ul><li>a</li><li>b</li></ul>")
    assert text == "One\n\nTwo\nstill two\n\na\n\nb"
    assert removed == 0


def test_inline_tags_are_stripped() -> None:
    text, removed = sanitize_description("<b>Bold</b> and <i>italic</i>.")
    assert text == "Bold and italic."
    assert removed == 0


def test_angle_brackets_without_tags_are_preserved() -> None:
    text, removed = sanitize_description("a < b and <user@example.com> stay.")
    assert text == "a < b and <user@example.com> stay."
    assert removed == 0


def test_self_closing_anchor_with_href_is_removed_and_counted() -> None:
    text, removed = sanitize_description('See <a href="https://example.com"/> now.')
    assert text == "See now."
    assert removed == 1


def test_self_closing_anchor_without_href_is_dropped_uncounted() -> None:
    text, removed = sanitize_description('See <a name="top"/> now.')
    assert text == "See now."
    assert removed == 0


def test_degenerate_inline_link_without_target_keeps_label() -> None:
    text, removed = sanitize_description("[just text]() here.")
    assert text == "just text here."
    assert removed == 0


def test_footnote_definition_with_url_target_keeps_marker() -> None:
    text, removed = sanitize_description("Note[^1].\n\n[^1]: https://example.com")
    assert text == "Note[^1].\n\n[^1]:"
    assert removed == 1


def test_entities_decode_up_to_five_passes() -> None:
    # Six-fold encoded "<" still decodes one level short of plain text.
    text, removed = sanitize_description("x &amp;amp;amp;amp;amp;lt; y")
    assert text == "x &lt; y"
    assert removed == 0


def test_protocol_relative_url_is_removed() -> None:
    text, removed = sanitize_description("Details: //www.example.com/manual here.")
    assert text == "Details: here."
    assert removed == 1


def test_protocol_relative_bare_domain_is_removed() -> None:
    text, removed = sanitize_description("See //example.com/page.")
    assert text == "See ."
    assert removed == 1


def test_protocol_relative_localhost_with_port_is_removed() -> None:
    text, removed = sanitize_description("See //localhost:3000/status.")
    assert text == "See ."
    assert removed == 1


def test_bare_double_slash_without_host_is_preserved() -> None:
    text, removed = sanitize_description("a // b and 50//2 stay.")
    assert text == "a // b and 50//2 stay."
    assert removed == 0


def test_idn_bare_domain_with_path_is_removed() -> None:
    text, removed = sanitize_description("Shop bücher.de/produkt now.")
    assert text == "Shop now."
    assert removed == 1


def test_idn_bare_domain_without_path_is_removed() -> None:
    text, removed = sanitize_description("Shop bücher.de now.")
    assert text == "Shop now."
    assert removed == 1


def test_technical_terms_and_abbreviations_are_preserved() -> None:
    text, removed = sanitize_description("USB3.0 stick, Python 3.12, z.B. for backups.")
    assert text == "USB3.0 stick, Python 3.12, z.B. for backups."
    assert removed == 0


def test_encoded_anchor_with_protocol_relative_url_label() -> None:
    text, removed = sanitize_description("&lt;a href=&quot;//example.com&quot;&gt;//www.example.com&lt;/a&gt;")
    assert not text
    assert removed == 1


def test_markdown_link_with_protocol_relative_target_keeps_label() -> None:
    text, removed = sanitize_description("[shop](//example.com/s) here.")
    assert text == "shop here."
    assert removed == 1


def test_protocol_relative_autolink_is_removed() -> None:
    text, removed = sanitize_description("See <//example.com/x> now.")
    assert text == "See now."
    assert removed == 1


def test_unclosed_script_element_is_dropped_to_end_of_input() -> None:
    text, removed = sanitize_description("Safe. <script>alert('https://evil.example')")
    assert text == "Safe."
    assert removed == 0


def test_unclosed_style_and_iframe_elements_are_dropped() -> None:
    text, removed = sanitize_description("<style>a{color:red")
    assert not text
    assert removed == 0
    text, removed = sanitize_description('Keep <iframe src="https://x.example">')
    assert text == "Keep"
    assert removed == 0


def test_unclosed_void_embed_keeps_following_text() -> None:
    text, removed = sanitize_description('See <embed src="https://x.example"> this table.')
    assert text == "See this table."
    assert removed == 0


def test_truncated_tag_fragment_at_end_is_dropped() -> None:
    text, removed = sanitize_description("Buy now <b")
    assert text == "Buy now"
    assert removed == 0
    text, removed = sanitize_description("Buy now <scri")
    assert text == "Buy now"
    assert removed == 0


def test_mailto_link_is_removed() -> None:
    text, removed = sanitize_description("Contact mailto:user@example.com please.")
    assert text == "Contact please."
    assert removed == 1


def test_mailto_with_query_and_uppercase_scheme_is_removed() -> None:
    text, removed = sanitize_description("Write MAILTO:a@b.com?subject=x today.")
    assert text == "Write today."
    assert removed == 1


def test_mailto_autolink_is_removed() -> None:
    text, removed = sanitize_description("See <mailto:user@example.com> now.")
    assert text == "See now."
    assert removed == 1


def test_markdown_link_with_mailto_label_is_dropped() -> None:
    text, removed = sanitize_description("[mailto:a@b.com](https://x.example) here.")
    assert text == "here."
    assert removed == 1


def test_mixed_content_counts_all_removals() -> None:
    text, removed = sanitize_description(
        "Prefix https://p.example\n\n"
        "Body with www.body.example, [link](https://l.example), "
        '<a href="https://a.example">A</a> and bare.example.\n\n'
        "Suffix."
    )
    assert text == "Prefix\n\nBody with , link, A and .\n\nSuffix."
    assert removed == 5
