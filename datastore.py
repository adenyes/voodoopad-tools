#!/usr/bin/env python3

# Copyright (c) 2004-2021 Primate Labs Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

# flake8: noqa

import errno
import hashlib
import os
from io import IOBase, FileIO, BytesIO
from pathlib import Path
import plistlib
import uuid as UUID
import xml.parsers.expat
from typing import Optional, BinaryIO, Union

# Disable encryption for now
# import vpenc

import tokenizer
from wordtrie import WordTrie


def sha1_hash(s):
    sha1 = hashlib.sha1()
    sha1.update(s.encode('utf-8'))
    return sha1.hexdigest()


class DataStore:
    def __init__(self):
        self.path = None
        self.encrypted = False
        self.enc_ctx = None
        self.password = None
        self.storeinfo = {}
        self.properties = {}
        self.items = {}
        self.item_plists = {}
        self.trie = None

    @classmethod
    def create(cls, path):
        ds = cls()
        ds.path = Path(path)

        ds.in_memory = False

        os.mkdir(ds.path)
        os.mkdir(Path(ds.path, 'pages'))
        for i in range(0, 16):
            os.mkdir(Path(ds.path, 'pages', f'{i:x}'))

        ds.storeinfo = {
            'isEncrypted': False,
            'uuid': str(UUID.uuid4()),
            'VoodooPadBundleVersion': 6,
        }

        ds.properties = {
            'allowPluginLinks': True,
            'bdToBookmarkAliasUpgrade': True,
            'createSpotlightIndex': True,
            'dbVersion': 7,
            'expectedPageCount': 0,
            'localWebAccess': False,
            'newPageUTI': 'net.daringfireball.markdown',
            'skIndexVersion': 7,
            'updatedFMPageToRealUTIs': True,
            'updatedSpecialPages3': True,
            'uuid': ds.storeinfo['uuid'],
        }

        index_title = 'Index'
        index_uuid = ds.add_item('Index', 'Write about Index here.', 'net.daringfireball.markdown')

        ds.properties['defaultPage'] = index_title
        ds.properties['defaultUUID'] = index_uuid

        storeinfo_path = Path(ds.path, 'storeinfo.plist')
        plistlib.dump(ds.storeinfo, open(str(storeinfo_path), 'wb'), fmt=plistlib.FMT_XML)

        properties_path = Path(ds.path, 'properties.plist')
        plistlib.dump(ds.properties, open(str(properties_path), 'wb'), fmt=plistlib.FMT_XML)

        return ds

    @classmethod
    def open(cls, path, password=None, in_memory=False):  # noqa: C901
        ds = cls()

        ds.path = Path(path)
        ds.encrypted = False
        ds.enc_ctx = None
        ds.password = password
        ds.in_memory = in_memory

        storeinfo_path = Path(ds.path, 'storeinfo.plist')
        if not storeinfo_path.exists():
            # FIXME: Raise an error that indicates the vpdoc is invalid or corrupt.
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), storeinfo_path)

        properties_path = Path(ds.path, 'properties.plist')
        if not properties_path.exists():
            # FIXME: Raise an error that indicates the vpdoc is invalid or corrupt.
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), properties_path)

        ds.storeinfo = plistlib.load(open(str(storeinfo_path), 'rb'), fmt=plistlib.FMT_XML)

        if ds.storeinfo['isEncrypted']:
            raise Exception('Encrypted documents are not supported')
            # if ds.password is None:
            #     raise Exception('Password is required for encrypted document')
            # ds.encrypted = True
            # ds.enc_ctx = vpenc.VPEncryptionContext()
            # ds.enc_ctx.load(ds.path, ds.password)

        if ds.storeinfo['VoodooPadBundleVersion'] != 5:
            raise Exception('Unsupported')

        ds.properties = ds.load_plist(properties_path)

        items_path = Path(ds.path, 'pages')
        if not items_path.is_dir():
            # FIXME: Raise an error that indicates the vpdoc is invalid or corrupt.
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), properties_path)

        ds.item_plists = {}
        # item_plist_paths  = items_path.rglob('*.plist')
        item_plist_paths = ds.get_plists(items_path)
        for item_plist_path in item_plist_paths:
            # VoodooPad (or the underlying macOS libraries) may generate
            # invalid XML. Skip the plist (and the associated item) if the XML
            # parser throws an exception.
            try:
                item_uuid = item_plist_path.stem
                item_plist = ds.load_plist(item_plist_path)
                ds.item_plists[item_uuid] = item_plist
            except xml.parsers.expat.ExpatError:
                print(f'Skipping {item_uuid} due to invalid plist')
                pass

        ds.items = {}
        for item_uuid in ds.item_plists.keys():
            item_plist = ds.item_plist(item_uuid)

            # If the current item is a page alias, then there is no file
            # associated with it.  Skip it and move on to the next item.
            if item_plist['uti'] in ['com.fm.page-alias']:
                continue

            # If the current item is a file alias, skip it and move on to the
            # next item.  The file alias is stored as an opaque blob created
            # with [NSURL bookmarkDataWithOptions] which we cannot parse at
            # this time.
            if item_plist['uti'] in ['com.fm.file-alias']:
                continue

            item_path = ds.item_path(item_uuid)

            if not item_path.exists():
                # FIXME: Raise an error that indicates the vpdoc is invalid or corrupt.
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), item_path)

            # RTFD files have some non-utf characters in the header.

            ds.items[item_uuid] = ds.load_file(item_path).decode('utf-8')

        return ds

    def close(self):
        pass

    def item_uuids(self):
        return self.item_plists.keys()

    def item(self, uuid):
        # TODO: Should item() return the underlying item (e.g., if the uuid is an
        # alias) or should it return something else?
        return self.items[uuid]

    def item_plist(self, uuid):
        return self.item_plists[uuid]

    def item_path(self, uuid):
        return Path(self.path, 'pages', uuid[0], uuid)

    def item_plist_path(self, uuid):
        return Path(self.path, 'pages', uuid[0], '{}.plist'.format(uuid))

    def validate(self):
        valid = True

        # Validate that the UUIDs match the UUIDs stored in the property lists.
        for item_uuid in self.item_uuids():
            item_plist = self.item_plist(item_uuid)
            if item_uuid != item_plist['uuid']:
                valid = False
                print('[WARN] UUID mismatch for {}'.format(item_uuid))

        # TODO: Check the default item exists.

        # TODO: Check the expected item count matches the actual item count.

        # TODO: Check that alias targets exist.

        return valid

    def add_item(self, name, text, format):
        item_uuid = str(UUID.uuid4())
        item_key = name.lower()

        data_hash = sha1_hash(text)

        # TODO: Add all fields.
        pl = dict(
          uuid = item_uuid,
          key = item_key,
          displayName = name,
          uti = format,
          dataHash = data_hash
        )

        item_path = Path(self.path, 'pages', item_uuid[0], item_uuid)
        plist_path = Path(self.path, 'pages', item_uuid[0], item_uuid + '.plist')

        # Save to disk
        self.save_plist(pl, plist_path)
        self.save_file(text.encode('utf-8'), item_path)

        # Keep in memory
        self.items[item_uuid] = text
        self.item_plists[item_uuid] = pl

        return item_uuid

    def load_plist(self, path):
        if self.encrypted:
            return self.enc_ctx.load_plist(path)
        else:
            return plistlib.load(open(str(path), 'rb'), fmt=plistlib.FMT_XML)

    def save_plist(self, plist, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if self.encrypted:
            return self.enc_ctx.save_plist(plist, path)
        else:
            with open(path, 'wb') as fp:
                plistlib.dump(plist, fp)

    def load_file(self, path):
        if self.encrypted:
            return self.enc_ctx.load_file(path)
        else:
            file_content = open(str(path), 'rb').read()
            # check if the first 4 bytes are the RTFD magic number
            if len(file_content) > 4 and file_content[0:4] == b'rtfd':
                return extract_rtf_content_from_rtfd(path)
            else:
                return file_content

    def save_file(self, data, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if self.encrypted:
            self.enc_ctx.save_file(path, data)
        else:
            with open(path, 'wb') as fp:
                fp.write(data)

    # This is a work-around for Path.rglob('*.plist'). Path.rglob() has issues when running inside
    # Geekbench
    def get_plists(self, dir):
        subdirs = os.listdir(str(dir))
        plists = []
        for s in subdirs:
            path = str(Path(dir, s))

            if not os.path.isdir(path):
                continue

            entries = os.listdir(path)
            for e in entries:
                if e.endswith('.plist'):
                    plists.append(Path(path, e))

        return plists

    def regenerate_trie(self):
        self.trie = WordTrie()
        for uuid in self.item_uuids():
            item = self.item_plist(uuid)
            name = tokenizer.tokenize_text(item['displayName'].lower())
            self.trie.add(name)


def read_rtf_item(data: Union[IOBase, BinaryIO, FileIO]) -> Union[Optional[bytes], dict[str, Optional[bytes]]]:
    item_type = read_int(data.read(4))
    if item_type == 1:
        # single item
        item_size = read_int(data.read(4))
        if item_size == 2147483648:  # 00 00 00 80 means it is a big file w padding
            padding_size = read_int(data.read(4))
            real_size = read_int(data.read(4))
            data.read(padding_size)  # skip padding
            return data.read(real_size)
        else:
            return data.read(item_size)
    elif item_type == 3:
        n_items = read_int(data.read(4))
        item_names = []
        directory_map = {}
        for _ in range(n_items):
            attr_len = read_int(data.read(4))
            attr_name = data.read(attr_len).decode('utf-8')
            item_names.append(attr_name)
            directory_map[attr_name] = None
        item_sizes = [read_int(data.read(4)) for _ in range(n_items)]
        item_data = [data.read(size) for size in item_sizes]
        for i, item in enumerate(item_data):
            directory_map[item_names[i]] = read_rtf_item(BytesIO(item))
        return directory_map
    return None


def read_int(four_bytes: bytes) -> int:
    return int.from_bytes(four_bytes, byteorder='little')


def extract_rtf_content_from_rtfd(rtfd_file_path: str) -> Optional[Union[int, bytes]]:
    """Extracts the first RTF content from an RTFD file. """
    with open(rtfd_file_path, 'rb') as rtfd_file:
        if rtfd_file.read(4) != b'rtfd':
            print("File is not an RTFD file.")
            return None
        rtfd_file.read(4)  # Wasn't sure what this is. Version, maybe.
        contents = read_rtf_item(rtfd_file)
        if isinstance(contents, dict):
            if 'TXT.rtf' in contents:
                return contents['TXT.rtf']
            else:
                print("Could not find RTF.rtf in RTFD file.")
                return None
