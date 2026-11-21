# Music Steward

Music Steward organizes a large music collection into a clean, verified copy and never changes the originals
unless the owner asks it to. These are the words the product, its screens and its documents use.

## Language

### The music and where it lives

**Collection folder**:
A folder (a whole share or drive, or a folder on this Mac) holding music that Music Steward scans and does not change.
_Avoid_: root, source root, source, library

**Destination folder**:
The folder where organized copies are written. It is always outside every collection folder.
_Avoid_: output folder, target, destination root

**Original**:
A file in a collection folder, as it was before Music Steward.
_Avoid_: source file

**Organized copy**:
A file written to the destination folder with corrected tags and the standard layout, checked against its original.
_Avoid_: output file, converted file

**Album**:
What the owner reviews and decides on: a folder of tracks, one image file with a cue sheet, or one disc of a set.
_Avoid_: unit, release

**Decision**:
The owner's choice for an album (accept a match, use the album's own information, or skip), with any edits.
_Avoid_: verdict, approval

### Setting originals aside

**Retire**:
Move the originals of albums whose organized copy is verified into the collection folder's `_ORIGINALS` folder. A move, never a delete.
_Avoid_: archive, delete, remove

**Parked originals**:
Originals that retiring has set aside in `_ORIGINALS`, with a README and a manifest.
_Avoid_: retired files, trash, recycle bin

**Archive**:
A verified backup copy of parked originals on another drive or share. The parked originals stay.
_Avoid_: retire, export

**Delete (originals)**:
Permanently remove parked originals to free space. Irreversible, and always confirmed.
_Avoid_: retire, clean up

### Connection and location

**Not connected**:
A collection folder whose drive or share is not available on this Mac right now. Nothing about it is lost.
_Avoid_: offline, missing, unavailable

**Not found**:
An album whose files are not at its collection folder's current location. Its decision is kept.
_Avoid_: missing, deleted, removed

**Share identity**:
The host and share name behind a collection folder on the network, kept on this Mac only and never with a password, so a new mount point of the same share can be suggested.
_Avoid_: NAS address, server, credentials
