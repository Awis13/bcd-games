# BCD: additional permission and terms under GNU GPL version 3, section 7

BCD additional terms, version 1. In effect from the adoption date below.

Adopted on: 2026-10-19
By: thebcd69 (BCD)

BCD (repository: https://github.com/thebcd69/bcd-games) is a modified version of
Madeira (https://github.com/willfaust/Madeira) by Will Faust and its contributors.
thebcd69 has modified it since [DATE]; every change is recorded in the repository's
history (GPL-3.0 section 5(a)). Madeira and the projects it builds on keep their own
licences, copyright notices and authors (see `README.md`, `THIRD-PARTY-NOTICES.md`,
`LICENSE`, `LICENSE-EXCEPTION.md` and `LICENSES/`). This file adds no term to, and
removes no permission from, their code.

## 1. What this file covers: "BCD Material"

"BCD Material" is every file of this repository, in any copy or modified version of
it, whose header carries the line

    BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

(normally together with `SPDX-License-Identifier: GPL-3.0-only` and
`Copyright <year> thebcd69 (BCD)`), and every copyrightable portion of such a file
that is copied into another file; in each case only to the extent that its copyright
is held by thebcd69 ("the BCD author"), or by a contributor who submitted it to BCD
under `CONTRIBUTING.md` on the terms of this file.

BCD Material never includes code of Madeira, Wine, FEX-Emu, DXMT, rpmalloc or any
other project or person, even where it sits in the same file as BCD Material or
beside it, or BCD has changed it; nor the Apple libraries named in section 3. The BCD
author puts the line above only on files that contain no code taken from other
people's work. A file that mixes BCD's text with someone else's keeps that other
author's header and licence, carries a "Modified by BCD" note with a date, and is
not BCD Material.

## 2. Licence

BCD Material is licensed under the GNU General Public License, version 3 only
(`COPYING`), with the additional permission in section 3 and the additional terms in
section 4. As GPL-3.0 section 7 requires, each file of BCD Material carries a notice
pointing to this file. (Version 3 only, so that these terms stay tied to the section 7
they are written under. BCD Material can be combined with Madeira's
GPL-3.0-or-later code; the combination is conveyed under GPL-3.0.)

## 3. Additional permission: Apple's Metal Shader Converter

The BCD author grants, for BCD Material, an additional permission with the same
wording as the Madeira Converter Exception, version 1 (`LICENSE-EXCEPTION.md`,
adopted 2026-09-24). The paragraph below is the grant itself, repeated word for word
so that it does not depend on that file or on later changes to it. In it, "this
Program" means the BCD Material and the other work of the BCD author named in
section 6, and "the licensors of this Program" means the BCD author.

> If you modify this Program, or any covered work, by linking or combining it
> with the Apple Metal Shader Converter dynamic library
> (libmetalirconverter.dylib, in any version) or with Apple's Metal,
> Foundation, CoreGraphics, QuartzCore, UIKit, AppKit and related system
> frameworks, or with modified versions of those libraries, the licensors of
> this Program grant you additional permission to convey the resulting work.
> Corresponding Source for a non-source form of such a combination shall
> include the source code for the parts of the Program used in the
> combination, but need not include the source code of those Apple libraries.
> This permission does not extend to those libraries, which remain subject to
> Apple's own licence terms. You may remove this additional permission from
> copies you convey, as GPL-3.0 section 7 allows.

## 4. Additional terms (GPL-3.0 section 7, items (b), (c), (d) and (e))

For BCD Material, the GPL is supplemented with the following terms:

**(b) Attribution.**
1. In every source copy of BCD Material, keep intact its copyright line and its
   BCD Material line (section 1).
2. If a work that contains a file of BCD Material, or a substantial part of one,
   has an interactive user interface that displays Appropriate Legal Notices
   (GPL-3.0 sections 0 and 5(d)), those notices must include the following line, for
   an app on its About, Credits or Licences screen:

       Includes work from BCD by thebcd69 (https://github.com/thebcd69/bcd-games)

   Nothing more is required there: no logo, no splash screen, no placement
   elsewhere in the interface. If the interface of such a work displays no
   Appropriate Legal Notices, this paragraph does not require it to start doing so
   (GPL-3.0 section 5(d)); paragraph 1 still applies.
3. The BCD author asks, without making it a condition, that a source distribution of
   such a work show the same line in its README or credits file.

**(c) Origin.**
1. You must not misrepresent the origin of BCD Material.
2. A modified version of BCD Material must be marked as modified, with a date,
   either in the header of each changed file or in a change log or version-control
   history that is distributed with the source of the modified version.
3. A modified version of BCD Material, or a work built from one, must not be
   presented as the official BCD. "The official BCD" means the source and the builds
   that thebcd69 publishes at https://github.com/thebcd69/bcd-games. Saying
   truthfully that a work is based on, or is a fork of, BCD is not a misrepresentation.

**(d) Publicity.** The names "BCD" and "thebcd69" must not be used to endorse or
promote a work based on BCD Material without the BCD author's prior written
permission. The attribution required in (b), and a truthful statement of origin such
as "a fork of BCD" or "based on BCD", are not such a use. To ask for permission, or
to ask a question about these terms, open an issue at
https://github.com/thebcd69/bcd-games/issues.

**(e) Trademarks.** No right is granted under trademark law to use the name "BCD",
or BCD's icon or logo (the files under `tools/brand/` and the app icon), as a trade
name, trademark or service mark, except (1) for the attribution in (b), (2) to state
the origin of a work truthfully, and (3) to convey an unmodified copy of BCD under the
name and icon it came with. This item concerns trademark rights only; it does not
limit what the GPL allows with the source files.

## 5. Nature of these terms

- The terms in section 4 are meant to be terms that GPL-3.0 section 7 permits (items
  (b) to (e) of its list), and they apply to BCD Material only. They add nothing to
  the terms of Madeira's or anyone else's code.
- If a court or authority held any term of section 4 to be more than section 7
  permits, only that term is affected: it may be removed as section 7 provides, and
  the rest of this file stays in force.
- Not honouring these terms is a violation of the licence for BCD Material, with the
  consequences GPL-3.0 section 8 provides for a violation, including its cure
  provisions.
- Nothing in this file limits a right that the GPL, or the licence of other code in
  this repository, gives to recipients.

## 6. Other work by the BCD author

Everything else in this repository of which the BCD author holds the copyright, and
which is not BCD Material (files without the line in section 1, and the BCD author's
changes inside files of Madeira or of other projects), is licensed on the terms of the
file it is in or, for a file with no licence notice, under the GNU General Public
License, version 3 or (at your option) any later version. For all of it the BCD author
also grants the additional permission in section 3. No term of section 4 applies to it.

Where a file of another project is licensed under the LGPL (Wine; the Direct3D 9 part
of DXMT), code that a BCD script or patch inserts into it is, in the patched file, also
licensed under that LGPL version, and the BCD author licenses it under that LGPL
version as well as under section 2; the script itself stays BCD Material. The BCD
author's own commits in the Wine, FEX-Emu, DXMT, rpmalloc and Madeira Dock
repositories follow the licence files of those repositories.

## 7. Version, contact and relation to other licences

These are the BCD additional terms, version 1. A later version applies only to material
that the BCD author conveys after publishing it; it never takes away a permission
already granted for a copy already conveyed.

Where a file contains both BCD Material and other people's code, each part keeps its
own terms. If a term of this file conflicts with a term of the GPL or of the licence of
other code in this repository, the other term prevails for that code.

Questions, and requests for permission under 4(d) or for other terms: open an issue at
https://github.com/thebcd69/bcd-games/issues.
