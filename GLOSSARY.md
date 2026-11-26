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

**Hidden**:
A collection folder that is no longer shown or scanned. Its albums, decisions, plans and history are kept, and showing it again (or adding the same path) brings everything back.
_Avoid_: removed, forgotten, deleted

**Not found**:
An exception raised on an album whose files are not at its collection folder's current location. The album and its decision are unchanged, and it clears when the files return.
_Avoid_: missing, deleted, removed

**No match**:
An album whose folder has no online release behind it. It is kept, and the owner can accept its own information and edit every field.
_Avoid_: not found, unknown, failed

**Share identity**:
The host and share name behind a collection folder on the network, kept on this Mac only and never with a password, so a new mount point of the same share can be suggested.
_Avoid_: NAS address, server, credentials

### Listening

**Streaming server**:
The program that serves the organized copies to phone players over the Subsonic protocol. It reads the organized library only, keeps its own accounts, and is separate from the review screen.
_Avoid_: NAS, cloud, hosting

**Listen**:
The screen that switches the streaming server on and off and shows how a phone connects to it.
_Avoid_: player, share

**Remote listening**:
Streaming to a phone that is not on the home network, through a private network such as Tailscale, never through an open port.
_Avoid_: public access, cloud access
