"""
Album-level pipeline stages for efficient music classification.

This module implements the four-stage pipeline at the album level with comprehensive
classification rules, normalization, and quality gates.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

from api.schemas import (
    AlbumInfo, ExtractedAlbumInfo, EnrichedAlbumInfo, FinalAlbumInfo
)
from api.client import ResilientAPIClient
from filesystem.file_ops import FileSystemOperations
from filesystem.album_detector import AlbumDetector
from utils.exceptions import (
    FileProcessingError, UnsupportedFormatError, MetadataExtractionError,
    CanonicalizationError, DatabaseError
)

logger = logging.getLogger(__name__)

# Directories/series that should not bias classification when seen in parent folders
FORMAT_SERIES_DIRS = {
    'xrcd', 'xr-cd', 'xr-cd24', 'xrcd24', 'xrcd2', 'k2hd', 'k2', 'shm-cd', 'mfsl', 'dcc',
    'hdcd', 'sacd', 'dsd', '24-88', '24-96', '24-192', 'tbm', 'three blind mice',
    'max mix', 'jvc xrcd', 'sampler'
}

def _normalized_parents(parents: List[str]) -> List[str]:
    """Normalize parent directory names and drop known format/series folders."""
    def clean(p: str) -> str:
        q = p.lower().strip().strip(" []()._-")
        return q
    return [p for p in (clean(x) for x in parents) if p and p not in FORMAT_SERIES_DIRS]


@dataclass
class ComposerAliases:
    """Canonical composer names and their aliases."""
    aliases = {
        "Johann Sebastian Bach": ["Bach", "J.S. Bach", "JS Bach", "J. S. Bach"],
        "Béla Bartók": ["Bela Bartok", "Bartok", "B. Bartok"],
        "Claude-Michel Schönberg": ["Claude Michel Schonberg", "Claude-Michel Schonberg", "Schonberg"],
        "Manuel de Falla": ["de Falla", "Falla", "M. de Falla"],
        "Wolfgang Amadeus Mozart": ["Mozart", "W.A. Mozart", "WA Mozart", "W. A. Mozart"],
        "Ludwig van Beethoven": ["Beethoven", "L. van Beethoven", "L.v. Beethoven"],
        "Pyotr Ilyich Tchaikovsky": ["Tchaikovsky", "P.I. Tchaikovsky", "PI Tchaikovsky"],
        "Antonio Vivaldi": ["Vivaldi", "A. Vivaldi"],
        "Carl Orff": ["Orff", "C. Orff"],
        "Joaquín Rodrigo": ["Rodrigo", "J. Rodrigo"],
        "Hector Berlioz": ["Berlioz", "H. Berlioz"],
        "Gioachino Rossini": ["Rossini", "G. Rossini"],
        "Giuseppe Verdi": ["Verdi", "G. Verdi"],
        "Antonín Dvořák": ["Dvorak", "A. Dvorak", "A. Dvořák"],
        "Nikolai Rimsky-Korsakov": ["Rimsky-Korsakov", "N. Rimsky-Korsakov"],
    }
    
    @classmethod
    def get_canonical_name(cls, name: str) -> str:
        """Return canonical composer name if found in aliases."""
        name_lower = name.lower().strip()
        for canonical, aliases in cls.aliases.items():
            if name_lower == canonical.lower() or any(name_lower == alias.lower() for alias in aliases):
                return canonical
        return name
    
@dataclass
class ArtistAliases:
    """Canonical artist names and their aliases for non-classical artists."""
    aliases = {
        "Jean-Michel Jarre": ["Jean Michel Jarre", "Jean Michel Jarre", "J.M. Jarre", "JM Jarre"],
        "Mecano": ["Ana José Nacho", "Ana-Jose-Nacho", "Ana Jose Nacho"],
        "The Cure": ["Cure", "The Cure"],
        "Arne Domnérus": ["Arne Domnerus", "Domnerus"],
        "Mike Oldfield": ["Michael Oldfield", "M. Oldfield"],
        "Emerson, Lake & Palmer": ["ELP", "Emerson Lake and Palmer", "Emerson, Lake and Palmer"],
        "Bill Evans": ["William Evans", "Bill Evans Trio"],
    }
    
    @classmethod
    def get_canonical_name(cls, name: str) -> str:
        """Return canonical artist name if found in aliases."""
        name_lower = name.lower().strip()
        for canonical, aliases in cls.aliases.items():
            if name_lower == canonical.lower() or any(name_lower == alias.lower() for alias in aliases):
                return canonical
        return name


@dataclass 
class OrchestraAliases:
    """Canonical orchestra names and their aliases."""
    aliases = {
        "London Symphony Orchestra": ["LSO", "London Symphony", "London SO"],
        "Boston Symphony Orchestra": ["BSO", "Boston Symphony", "Boston SO"],
        "Chicago Symphony Orchestra": ["CSO", "Chicago Symphony", "Chicago SO"],
        "New York Philharmonic": ["NYP", "NY Philharmonic", "New York Phil"],
        "Berlin Philharmonic": ["BPO", "Berliner Philharmoniker", "Berlin Phil"],
        "Vienna Philharmonic": ["VPO", "Wiener Philharmoniker", "Vienna Phil"],
    }
    
    @classmethod
    def get_canonical_name(cls, name: str) -> str:
        """Return canonical orchestra name if found in aliases."""
        name_lower = name.lower().strip()
        for canonical, aliases in cls.aliases.items():
            if name_lower == canonical.lower() or any(name_lower == alias.lower() for alias in aliases):
                return canonical
        return name


class AlbumStage1Analysis:
    """Stage 1: Album Analysis & Metadata Sampling."""
    
    def __init__(self, filesystem_ops: FileSystemOperations, album_detector: AlbumDetector):
        self.filesystem_ops = filesystem_ops
        self.album_detector = album_detector
    
    def process(self, album_path: Path) -> Optional[AlbumInfo]:
        """
        Analyze an album directory and sample metadata.
        
        Args:
            album_path: Path to the album directory
            
        Returns:
            AlbumInfo object with analyzed album data
        """
        try:
            logger.debug(f"Album Stage 1: Analyzing {album_path}")
            
            # Get basic album structure
            album_structure = self.album_detector.analyze_album_structure(album_path)
            
            if album_structure['track_count'] == 0:
                logger.info(f"Skipping album with no tracks: {album_path}")
                return None
            
            # Sample metadata from a few tracks
            sample_metadata = self._sample_track_metadata(album_structure['track_paths'][:3])
            
            return AlbumInfo(
                album_path=album_structure['album_path'],
                album_name=album_structure['album_name'],
                parent_dirs=album_structure['parent_dirs'],
                track_count=album_structure['track_count'],
                track_files=album_structure['track_files'],
                track_paths=album_structure['track_paths'],
                has_disc_structure=album_structure['has_disc_structure'],
                disc_subdirs=album_structure['disc_subdirs'],
                total_size_mb=album_structure['total_size_mb'],
                sample_metadata=sample_metadata
            )
            
        except Exception as e:
            raise FileProcessingError(f"Album Stage 1 failed for {album_path}: {e}")
    
    def _sample_track_metadata(self, track_paths: List[Path]) -> Dict[str, Any]:
        """Sample metadata from a few tracks to get album-level info."""
        combined_metadata = {}
        
        for track_path in track_paths[:3]:  # Sample first 3 tracks
            try:
                metadata = self.filesystem_ops.extract_metadata(track_path)
                
                # Collect common fields
                for field in ['artist', 'albumartist', 'album', 'date', 'year', 'genre']:
                    if field in metadata and metadata[field]:
                        if field not in combined_metadata:
                            combined_metadata[field] = []
                        combined_metadata[field].append(metadata[field])
                
            except Exception as e:
                logger.debug(f"Could not extract metadata from {track_path}: {e}")
                continue
        
        # Consolidate repeated values
        consolidated = {}
        for field, values in combined_metadata.items():
            # Find most common value
            if values:
                most_common = max(set(values), key=values.count)
                consolidated[field] = most_common
        
        return consolidated


class AlbumStage2Extraction:
    """Stage 2: Album-Level Structured Data Extraction with comprehensive normalization."""
    
    COMPREHENSIVE_RULES = """
# Canonical Folder Model
/{TOP_GENRE}/...  # one of: Classical, Jazz, Electronic, Library, Compilations & VA, Soundtracks
Soundtracks has subgenres: /Soundtracks/{Film|TV|Game|Stage & Musicals}/TITLE (YEAR?)/[VERSION or DISC]

# Normalization Rules (Apply First!)
1. Trim & tidy:
   - Collapse repeated spaces/underscores; use " - " as the only separator
   - Title Case, preserve diacritics (e.g., "Schönberg", "Béla Bartók")
   - Move format/media tags to the very end: [XRCD] [XRCD24] [K2HD] [SACD] [DSD] [MFSL] [24-88] [SHM-CD]
   - Year: four digits, placed before tags

2. Alias & spelling unification:
   - Bach, J.S. Bach → Johann Sebastian Bach
   - Bela Bartok → Béla Bartók
   - Claude Michel Schonberg → Claude-Michel Schönberg
   - de Falla → Manuel de Falla
   - LSO → London Symphony Orchestra; BSO → Boston Symphony Orchestra; CSO → Chicago Symphony Orchestra
   - Merge punctuation variants (,/&.) into & between artists; use full orchestra names

3. CJK names:
   - For Chinese/Japanese/Korean artists, prefer Latin (Native) form: Kitaro (喜多郎), Tsai Chin (蔡琴)

4. Series grouping:
   - If series keyword appears (e.g., "Best Audiophile Voices", "Audiophile Reference"), group as:
     /Compilations & VA/{Series Name}/Volume or Disc Name - YEAR [tags]

5. Multi-disc:
   - Keep discs together under same album folder: .../ALBUM - YEAR/[CD1], [CD2], ...
"""
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        self.api_client = api_client
        self.model_name = model_name
    
    def _sanitize_unicode(self, text: str) -> str:
        """Sanitize Unicode text to prevent encoding errors."""
        try:
            text.encode('utf-8')
            return text
        except UnicodeEncodeError:
            sanitized = []
            for char in text:
                try:
                    char.encode('utf-8')
                    sanitized.append(char)
                except UnicodeEncodeError:
                    sanitized.append('?')
            return ''.join(sanitized)
    
    def process(self, album_info: AlbumInfo) -> ExtractedAlbumInfo:
        """
        Extract structured album data using LLM with comprehensive rules.
        
        Args:
            album_info: Album information from Stage 1
            
        Returns:
            ExtractedAlbumInfo object
        """
        logger.debug(f"Album Stage 2: Extracting data for {album_info.album_name}")
        
        prompt = self._build_extraction_prompt(album_info)
        
        extracted_info = self.api_client.get_structured_response(
            prompt=prompt,
            model=self.model_name,
            response_model=ExtractedAlbumInfo,
            temperature=0.0
        )
        
        # Apply normalization
        extracted_info = self._normalize_extracted_info(extracted_info)
        
        logger.debug(f"Album Stage 2: Extracted - Artist: {extracted_info.artist}, "
                    f"Album: {extracted_info.album_title}, Year: {extracted_info.year}")
        
        return extracted_info
    
    def _normalize_extracted_info(self, info: ExtractedAlbumInfo) -> ExtractedAlbumInfo:
        """Apply normalization rules to extracted info."""
        # If we got "Unknown Artist", try to extract from album title as fallback
        if info.artist == "Unknown Artist" and " - " in info.album_title:
            info = self._try_extract_artist_from_title(info)
        
        # Normalize artist name
        info.artist = self._normalize_artist_name(info.artist)
        
        # Clean album title
        info.album_title = self._normalize_album_title(info.album_title)
        
        return info
    
    def _try_extract_artist_from_title(self, info: ExtractedAlbumInfo) -> ExtractedAlbumInfo:
        """Try to extract artist from album title if it contains both."""
        # Common indicators that text is an artist/performer name
        artist_indicators = [
            'Orchestra', 'Symphony', 'Philharmonic', 'Ensemble', 
            'Quartet', 'Trio', 'Quintet', 'Band', 'Choir',
            '& His', '& Her', '& The', '& Los', '& Les',
            'Conductor', 'Piano', 'Violin', 'Cello'
        ]
        
        # Try to parse "Album - Artist" pattern
        parts = info.album_title.split(' - ')
        if len(parts) >= 2:
            # Check if the second part looks like an artist
            potential_artist = parts[-1].strip()
            
            # Remove format tags from potential artist
            potential_artist = re.sub(r'\[(XRCD|K2HD|SACD|DSD|MFSL|SHM-CD|24-\d+)\].*$', '', potential_artist).strip()
            
            # Check if it contains artist indicators or looks like a name
            has_indicator = any(indicator in potential_artist for indicator in artist_indicators)
            has_ampersand = '&' in potential_artist  # Often indicates collaboration
            looks_like_name = bool(re.match(r'^[A-Z][a-z]+ [A-Z]', potential_artist))  # Simple name pattern
            
            if has_indicator or has_ampersand or looks_like_name:
                # It's likely "Album - Artist" pattern
                info.artist = potential_artist
                info.album_title = ' - '.join(parts[:-1]).strip()
                logger.debug(f"Extracted artist '{info.artist}' from album title")
        
        return info
    
    def _normalize_artist_name(self, artist: str) -> str:
        """Normalize artist name with aliases and formatting."""
        if not artist:
            return artist
            
        # Check for regular artist aliases first  
        canonical = ArtistAliases.get_canonical_name(artist)
        if canonical != artist:
            return canonical
            
        # Check for composer aliases
        canonical = ComposerAliases.get_canonical_name(artist)
        if canonical != artist:
            return canonical
            
        # Check for orchestra aliases
        canonical = OrchestraAliases.get_canonical_name(artist)
        if canonical != artist:
            return canonical
        
        # Clean up spacing and punctuation
        artist = re.sub(r'\s+', ' ', artist.strip())
        artist = re.sub(r'\s*[,/]\s*', ' & ', artist)  # Replace , / with &
        
        return artist
    
    def _normalize_album_title(self, title: str) -> str:
        """Normalize album title."""
        if not title:
            return title
            
        # Remove format tags from title
        format_patterns = [
            r'\[(FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD|MFSL|SHM-CD|24-\d+)\]',
            r'\((FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD|MFSL|SHM-CD|24-\d+)\)',
            r'[-_]\s*(FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD|MFSL|SHM-CD|24-\d+)\s*$'
        ]
        for pattern in format_patterns:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Clean up underscores and spacing
        title = title.replace('_', ' ')
        title = re.sub(r'\s+', ' ', title.strip())
        
        return title
    
    def _build_extraction_prompt(self, album_info: AlbumInfo) -> str:
        """Build the extraction prompt for album-level processing."""
        
        # Format existing metadata
        metadata_str = ""
        if album_info.sample_metadata:
            metadata_items = []
            for key, value in album_info.sample_metadata.items():
                if value and str(value).strip():
                    metadata_items.append(f"  {key}: {value}")
            if metadata_items:
                metadata_str = f"Sample track metadata:\n" + "\n".join(metadata_items)
        
        # Format track listing (show first 10 tracks)
        track_list = "\n".join([f"  {i+1:02d}. {track}" 
                               for i, track in enumerate(album_info.track_files[:10])])
        if len(album_info.track_files) > 10:
            track_list += f"\n  ... and {len(album_info.track_files) - 10} more tracks"
        
        # Parent directory context (ignore format/series folders)
        norm_parents = _normalized_parents(album_info.parent_dirs)
        parent_path = " > ".join(norm_parents) if norm_parents else "None"
        
        return f"""
Extract album information from this music collection following these normalization rules:

{self.COMPREHENSIVE_RULES}

Album directory: {self._sanitize_unicode(album_info.album_name)}
Parent folders: {parent_path}
Total tracks: {album_info.track_count}
{f"Multi-disc album: {len(album_info.disc_subdirs)} discs" if album_info.has_disc_structure else "Single disc album"}

Track listing:
{track_list}

{metadata_str}

Common folder naming patterns to parse (check these patterns in order):
1. "Artist - Album Title" (most common)
2. "Album Title - Artist" (check if second part looks like artist/band/orchestra name)
3. "Artist - Album Title - Year"
4. "Album Title - Artist & Orchestra/Conductor [Format]"
5. For classical: "Work Title - Performer(s) [Format]"
6. Just "Album Title" with no artist

Examples of pattern #2 and #4 (Album - Artist):
- "La Folia de la Spagna - Paniagua & Atrium Musicae de Madrid [XRCD24]" → Artist: "Paniagua & Atrium Musicae de Madrid", Album: "La Folia de la Spagna"
- "The Four Seasons - Salvatore Accardo [XRCD]" → Artist: "Salvatore Accardo", Album: "The Four Seasons"
- "Carmina Burana - Boston Symphony Orchestra [SACD]" → Artist: "Boston Symphony Orchestra", Album: "Carmina Burana"

Extract and normalize the following album information:
- artist: The primary album artist or band name (check folder name patterns above, use "Unknown Artist" if unable to determine)
- album_title: The album title (remove format tags, clean spacing, preserve diacritics, use "Unknown Album" if unable to determine)
- year: Album release year if found (4-digit number), or null if not found  
- total_tracks: Confirm the total number of tracks ({album_info.track_count})
- disc_count: Number of discs (1 for single disc, {len(album_info.disc_subdirs)} if multi-disc)

Important parsing rules:
- First check if the folder name contains a dash (-) separator
- If text after the dash contains orchestra/ensemble/band names or performer names, it's likely the artist
- Words like "Orchestra", "Ensemble", "Quartet", "Trio", "Band", "& His", "& The" often indicate artist names
- For classical albums, if you see performer names after the work title, extract them as the artist
- If the album has Chinese/Japanese/Korean characters and you cannot determine the artist/title, use "Unknown Artist" / "Unknown Album"
- For classical music, identify the COMPOSER as the primary artist if it's a single-composer album
- For soundtracks, keep the film/show/game title as the album title, not the composer
- Apply all normalization rules strictly
- Never return null for artist or album_title fields
"""


class AlbumStage3Enrichment:
    """Stage 3: Album-Level Semantic Enrichment with genre decision tree."""
    
    GENRE_CLASSIFICATION_RULES = """
# Genre Classification Decision Tree (evaluate in order, first match wins)

A) Soundtracks → /Soundtracks/{Film|TV|Game|Stage & Musicals}
   - Positive signals: OST, Original Motion Picture Soundtrack, Music From, Score, Soundtrack, TV, HBO, Netflix, game titles, film titles, anime/Studio Ghibli
   - Film composers: Alan Menken, Hans Zimmer, Joe Hisaishi, Ennio Morricone, Michael Nyman, Gabriel Yared, Ramin Djawadi, James Newton Howard, Daniel Pemberton, Henry Mancini, Jérôme Rebotier, Yuji Nomi, Katsu Hoshi, Martin O'Donnell & Michael Salvatori
   - Stage & Musicals: Original Broadway Cast, Cast Recording, Royal Albert Hall, Staged Concert, 25th Anniversary, Les Misérables, Cirque du Soleil
   - Game: Halo, Zelda, Nintendo Orchestra, game franchises
   - TV: HBO/Season/Sxx indicators

B) Classical → /Classical/{Composer}/{Work - Conductor - Soloists/Orchestra - YEAR [tags]}
   - Positive signals: Symphony, Concerto, Sonata, Suite, Mass, Requiem, Overtures, BWV, K./KV, RV, Op., composer names, orchestra/conductor mentions
   - COMPOSER-FIRST RULE: If album is 1-composer → top-level = that Composer, not performer
   - If mixed composers (recital), use /Classical/Recitals/{Performer}/{Album - YEAR [tags]}

C) Jazz → /Jazz/{Artist}/{Album - YEAR [tags]}
   - Positive signals: jazz artists, combos (Trio, Quartet, Quintet), Blue Note-style naming, standards

D) Electronic → /Electronic/{Artist}/{Album - YEAR [tags]}
   - Electronic artists/labels/styles: Jean-Michel Jarre, Daft Punk, Kitaro, Carpenter Brut, synthwave, ambient

E) Compilations & VA → /Compilations & VA/{Series or Theme}/{Album - YEAR [tags]}
   - Keywords: Greatest Hits, Best Of, Sampler, Reference, VA, Various Artists, Audiophile, Label Sampler
   - If Greatest Hits lacks an artist, keep here (do not guess artist)

F) Library (Pop/Rock/World/etc.) → /Library/{Artist}/{Album - YEAR [tags]}
   - Everything else: Adele, Dire Straits, Beach Boys, Muse, Santana, Steely Dan
   - CROSSOVER RULE: Rock adaptations of classical themes (e.g., ELP "Pictures at an Exhibition") stay in Library, not Classical
"""
    
    def __init__(self, api_client: ResilientAPIClient, model_name: str):
        self.api_client = api_client
        self.model_name = model_name
    
    def process(self, extracted_info: ExtractedAlbumInfo) -> EnrichedAlbumInfo:
        """
        Add semantic enrichment to album data.
        
        Args:
            extracted_info: Structured album data from Stage 2
            
        Returns:
            EnrichedAlbumInfo object
        """
        logger.debug(f"Album Stage 3: Enriching {extracted_info.artist} - {extracted_info.album_title}")
        
        prompt = self._build_enrichment_prompt(extracted_info)
        
        enriched_info = self.api_client.get_structured_response(
            prompt=prompt,
            model=self.model_name,
            response_model=EnrichedAlbumInfo,
            temperature=0.3
        )
        
        logger.debug(f"Album Stage 3: Enriched with {len(enriched_info.genres)} genres")
        
        return enriched_info
    
    def _build_enrichment_prompt(self, extracted_info: ExtractedAlbumInfo) -> str:
        """Build the enrichment prompt for album-level semantic analysis."""
        
        disc_info = f" ({extracted_info.disc_count} disc album)" if extracted_info.disc_count and extracted_info.disc_count > 1 else ""
        year_info = f" ({extracted_info.year})" if extracted_info.year else ""
        
        return f"""
Analyze this music album for classification following these rules:

{self.GENRE_CLASSIFICATION_RULES}

Artist: {extracted_info.artist}
Album: {extracted_info.album_title}
Year: {extracted_info.year or "Unknown"}
Tracks: {extracted_info.total_tracks}{disc_info}

Provide semantic analysis for this complete album:

1. Genres (3-5 specific genres):
   - Use decision tree order: check Soundtracks first, then Classical, Jazz, Electronic, Compilations, finally Library
   - Be specific (e.g., "Film Soundtrack", "Symphonic Metal", "Cool Jazz", "Minimal Techno")
   - Include indicators like "OST", "Original Broadway Cast" if applicable

2. Moods (3-5 descriptive moods):
   - Overall emotional character of the album
   - Use adjectives like "melancholic", "uplifting", "aggressive", "contemplative"

3. Style tags (3-5 descriptors):
   - Musical characteristics (e.g., "orchestral", "guitar-driven", "electronic", "acoustic")
   - Production style (e.g., "lo-fi", "polished", "live recording")

4. Target audience (2-3 categories):
   - Who would enjoy this album
   - Suitable occasions

5. Energy level (1-5 scale):
   - 1: Very calm/ambient
   - 2: Relaxed
   - 3: Moderate
   - 4: Energetic
   - 5: Very high energy

6. Is compilation:
   - true ONLY if album contains tracks from MULTIPLE different artists (Various Artists, VA, samplers)
   - false if single artist/band album (including their Greatest Hits, Best Of, Collections)
   - IMPORTANT: "Queen - Greatest Hits" is NOT a compilation (it's a single-artist collection)
   - IMPORTANT: "Best Audiophile Voices" IS a compilation (multiple artists)

7. Additional context:
   - For classical: identify if single-composer work or mixed recital
   - For soundtracks: identify if Film/TV/Game/Stage
   - Note any special series (Best Audiophile Voices, etc.)

Base your analysis on your knowledge of "{extracted_info.artist}" and the album "{extracted_info.album_title}"{year_info}.
"""


class AlbumStage4Canonicalization:
    """Stage 4: Album Canonicalization & Final Organization with quality gates."""
    
    # Film composers for soundtrack detection
    FILM_COMPOSERS = {
        "Alan Menken", "Hans Zimmer", "Joe Hisaishi", "Ennio Morricone", 
        "Michael Nyman", "Gabriel Yared", "Ramin Djawadi", "James Newton Howard",
        "Daniel Pemberton", "Henry Mancini", "Jérôme Rebotier", "Yuji Nomi",
        "Katsu Hoshi", "Martin O'Donnell", "Michael Salvatori", "John Williams",
        "Howard Shore", "James Horner", "Alexandre Desplat", "Thomas Newman"
    }
    
    # Classical composers for composer-first organization
    CLASSICAL_COMPOSERS = {
        "Johann Sebastian Bach", "Wolfgang Amadeus Mozart", "Ludwig van Beethoven",
        "Antonio Vivaldi", "Pyotr Ilyich Tchaikovsky", "Johannes Brahms",
        "Frédéric Chopin", "Franz Schubert", "Joseph Haydn", "George Frideric Handel",
        "Carl Orff", "Béla Bartók", "Claude Debussy", "Maurice Ravel",
        "Sergei Rachmaninoff", "Igor Stravinsky", "Antonín Dvořák", "Gustav Mahler",
        "Richard Wagner", "Giuseppe Verdi", "Giacomo Puccini", "Hector Berlioz",
        "Felix Mendelssohn", "Robert Schumann", "Franz Liszt", "Joaquín Rodrigo",
        "Manuel de Falla", "Isaac Albéniz", "Enrique Granados", "Heitor Villa-Lobos"
    }
    
    def __init__(self):
        pass
    
    def process(self, enriched_info: EnrichedAlbumInfo, album_info: AlbumInfo) -> FinalAlbumInfo:
        """
        Finalize album information and determine organization.
        
        Args:
            enriched_info: Enriched album data from Stage 3
            album_info: Original album info from Stage 1
            
        Returns:
            FinalAlbumInfo object with organization details
        """
        logger.debug(f"Album Stage 4: Finalizing {enriched_info.artist} - {enriched_info.album_title}")
        
        # Determine organization category with comprehensive rules
        top_category, sub_category, composer = self._classify_album_comprehensive(enriched_info, album_info)
        
        # Apply quality gates
        top_category, sub_category = self._apply_quality_gates(
            enriched_info, album_info, top_category, sub_category
        )
        
        # Generate suggested directory path
        suggested_dir = self._generate_album_path_comprehensive(
            enriched_info, album_info, top_category, sub_category, composer
        )
        
        # Extract format tags from album name/folder
        format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
        
        # Build processing notes
        processing_notes = self._build_processing_notes(
            enriched_info, top_category, sub_category, composer
        )
        
        return FinalAlbumInfo(
            **enriched_info.dict(),
            canonical_artist=self._canonicalize_artist(enriched_info.artist),
            canonical_album_title=self._canonicalize_title(enriched_info.album_title),
            musicbrainz_release_id=None,  # Would implement MusicBrainz lookup here
            top_category=top_category,
            sub_category=sub_category,
            suggested_album_dir=suggested_dir,
            organization_reason=f"Album-level classification: {top_category}" + (f"/{sub_category}" if sub_category else ""),
            confidence_score=0.85,  # Higher confidence for album-level processing
            format_tags=format_tags,
            processing_notes=processing_notes
        )
    
    def _classify_album_comprehensive(self, enriched_info: EnrichedAlbumInfo, 
                                     album_info: AlbumInfo) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Classify album using comprehensive decision tree.
        Returns: (top_category, sub_category, composer_if_classical)
        """
        
        genres_lower = [g.lower() for g in enriched_info.genres]
        genres_text = ' '.join(genres_lower)
        album_lower = enriched_info.album_title.lower() if enriched_info.album_title else ""
        artist_lower = enriched_info.artist.lower() if enriched_info.artist else ""

        # Safety net (pre): short-circuit obvious artist-based misroutes
        pre = self._safety_net_pre(genres_lower, artist_lower, album_lower)
        if pre:
            return pre[0], pre[1], pre[2]
        
        # A) Check for Soundtracks FIRST
        soundtrack_indicators = [
            'soundtrack', 'score', 'film music', 'game music', 'ost',
            'original motion picture', 'music from', 'original soundtrack'
        ]
        # Anime/Studio Ghibli cues should also trigger soundtrack routing
        anime_indicators = ['anime', 'ghibli', 'studio ghibli', 'on your mark']
        
        # Check if artist is a known film composer
        is_film_composer = any(composer.lower() in artist_lower 
                              for composer in self.FILM_COMPOSERS)
        
        if (any(term in genres_text for term in soundtrack_indicators + anime_indicators) or 
            any(term in album_lower for term in soundtrack_indicators + anime_indicators) or
            is_film_composer):
            
            # Determine soundtrack sub-category
            if any(term in genres_text + ' ' + album_lower for term in 
                  ['musical', 'broadway', 'cast recording', 'royal albert hall', 
                   'staged concert', 'les misérables', 'les miserables', 'cirque du soleil']):
                return "Soundtracks", "Stage & Musicals", None
            elif any(term in genres_text + ' ' + album_lower for term in 
                    ['game', 'video game', 'halo', 'zelda', 'nintendo']):
                return "Soundtracks", "Game", None
            elif any(term in genres_text + ' ' + album_lower for term in 
                    ['tv', 'television', 'hbo', 'netflix', 'season']):
                return "Soundtracks", "TV", None
            elif any(term in genres_text + ' ' + album_lower for term in 
                    ['anime', 'ghibli', 'studio ghibli', 'on your mark']):
                return "Soundtracks", "Film", None  # Anime goes under Film
            else:
                return "Soundtracks", "Film", None  # Default to Film
        
        # B) Check for Classical (with composer-first logic)
        classical_indicators = [
            'classical', 'symphony', 'symphonic', 'concerto', 'opera', 'chamber',
            'orchestral', 'baroque', 'romantic', 'modern classical', 'sonata',
            'suite', 'overture', 'requiem', 'mass', 'cantata', 'fugue'
        ]
        
        # Check for classical work patterns
        classical_patterns = [r'\bOp\.\s*\d+', r'\bBWV\s*\d+', r'\bK\.\s*\d+', 
                             r'\bKV\s*\d+', r'\bRV\s*\d+', r'No\.\s*\d+']
        has_classical_pattern = any(re.search(pattern, enriched_info.album_title or '', re.IGNORECASE) 
                                   for pattern in classical_patterns)
        
        if (any(term in genres_text for term in classical_indicators) or has_classical_pattern):
            # Determine if single composer or recital
            composer = self._identify_composer(enriched_info)
            if composer:
                return "Classical", None, composer
            else:
                # Mixed composers or recital
                return "Classical", "Recitals", None
        
        # Check if artist is a known classical composer (even if not tagged as classical)
        canonical_artist = ComposerAliases.get_canonical_name(enriched_info.artist)
        if canonical_artist in self.CLASSICAL_COMPOSERS:
            return "Classical", None, canonical_artist
        
        # C) Check for Compilations & VA BEFORE Jazz/Electronic to catch audiophile compilations
        # IMPORTANT: Distinguish between single-artist collections and true compilations
        
        # True compilation indicators (multiple artists)
        true_compilation_indicators = [
            'various artists', 'va', 'sampler', 'label sampler', 'multi-artist'
        ]
        
        # Collection-type album titles that could be single artist OR compilation
        collection_titles = [
            'greatest hits', 'best of', 'collection', 'anthology', 'essential', 'essentials',
            'ultimate', 'gold', 'platinum', 'complete'
        ]
        
        # Check for series patterns (these are always compilations)
        series_patterns = [
            'best audiophile voices', 'audiophile reference', 'super analog sound',
            'xrcd sampler', 'test cd', 'demo disc', 'audiophile test'
        ]
        
        # Determine if this is a true compilation or single-artist collection
        is_true_compilation = False
        
        # First check: explicit compilation indicators
        if (any(term in album_lower for term in true_compilation_indicators) or
            any(term in artist_lower for term in ['various artists', 'va'])):
            is_true_compilation = True
        
        # Second check: series patterns are always compilations
        elif any(pattern in album_lower for pattern in series_patterns):
            is_true_compilation = True
        
        # Third check: collection titles need artist verification
        elif any(term in album_lower for term in collection_titles):
            # If we have a valid artist that's not "Unknown" or "Various", it's a single-artist collection
            if (enriched_info.artist and 
                enriched_info.artist != "Unknown Artist" and
                enriched_info.artist != "Unknown" and
                'various' not in artist_lower and
                'va' != artist_lower):
                # This is a single-artist collection, NOT a compilation
                is_true_compilation = False
            else:
                # No clear artist or it's Various Artists
                is_true_compilation = True
        
        # Fourth check: LLM flag (but verify it's correct)
        elif enriched_info.is_compilation:
            # Double-check the LLM's compilation flag
            if (enriched_info.artist and 
                enriched_info.artist != "Unknown Artist" and
                enriched_info.artist != "Unknown" and
                'various' not in artist_lower):
                # LLM might be wrong - if we have a clear single artist, override
                is_true_compilation = False
            else:
                is_true_compilation = True
        
        if is_true_compilation:
            return "Compilations & VA", None, None

        # Safety: single-artist collections with collection titles should remain with the artist
        if (any(term in album_lower for term in collection_titles) and
            (enriched_info.artist and 
             enriched_info.artist != "Unknown Artist" and
             enriched_info.artist != "Unknown" and
             'various' not in artist_lower and
             artist_lower.strip() not in {'va', 'various artists'})):
            return "Library", None, None
        
        # Jazz label/series hints (folder/album tokens)
        JAZZ_LABEL_HINTS = {
            'blue note', 'prestige', 'riverside', 'contemporary', 'tbm', 'three blind mice',
            'dcc', 'audio wave'
        }
        label_context = f"{album_info.album_name} {' '.join(album_info.parent_dirs)}".lower()
        if any(lbl in label_context for lbl in JAZZ_LABEL_HINTS):
            return "Jazz", None, None

        # D) Check for Jazz
        jazz_indicators = [
            'jazz', 'blues', 'swing', 'bebop', 'fusion', 'smooth jazz',
            'cool jazz', 'free jazz', 'hard bop', 'latin jazz'
        ]
        if any(term in genres_text for term in jazz_indicators):
            return "Jazz", None, None
        
        # E) Check for Electronic
        electronic_indicators = [
            'electronic', 'techno', 'house', 'ambient', 'edm', 'synth',
            'electro', 'trance', 'dubstep', 'drum and bass', 'dnb',
            'breakbeat', 'downtempo', 'chillout', 'idm'
        ]
        
        # Known electronic artists
        electronic_artists = [
            'jean-michel jarre', 'jean michel jarre', 'daft punk', 'kitaro',
            'carpenter brut', 'kraftwerk', 'tangerine dream', 'vangelis', 'magic sword',
            'deadmau5', 'aphex twin', 'boards of canada', 'massive attack'
        ]
        
        if (any(term in genres_text for term in electronic_indicators) or
            any(artist in artist_lower for artist in electronic_artists)):
            return "Electronic", None, None
        
        # F) Default to Library for everything else
        top, sub = self._safety_net_post("Library", None, artist_lower, album_lower)
        return top, sub, None
    
    def _identify_composer(self, enriched_info: EnrichedAlbumInfo) -> Optional[str]:
        """Identify if this is a single-composer classical album."""
        # Check if artist is a known composer
        canonical_artist = ComposerAliases.get_canonical_name(enriched_info.artist)
        if canonical_artist in self.CLASSICAL_COMPOSERS:
            return canonical_artist
        
        # Check for well-known classical works that imply a specific composer
        WORK_TO_COMPOSER = {
            'four seasons': 'Antonio Vivaldi',
            'le quattro stagioni': 'Antonio Vivaldi',
            'die vier jahreszeiten': 'Antonio Vivaldi',
            'brandenburg': 'Johann Sebastian Bach',
            'goldberg variations': 'Johann Sebastian Bach',
            'well-tempered clavier': 'Johann Sebastian Bach',
            'art of fugue': 'Johann Sebastian Bach',
            'moonlight sonata': 'Ludwig van Beethoven',
            'emperor concerto': 'Ludwig van Beethoven',
            'eroica': 'Ludwig van Beethoven',
            'pastoral symphony': 'Ludwig van Beethoven',
            'requiem k. 626': 'Wolfgang Amadeus Mozart',
            'magic flute': 'Wolfgang Amadeus Mozart',
            'don giovanni': 'Wolfgang Amadeus Mozart',
            'eine kleine nachtmusik': 'Wolfgang Amadeus Mozart',
            'carmina burana': 'Carl Orff',
            'bolero': 'Maurice Ravel',
            'pictures at an exhibition': 'Modest Mussorgsky',
            'planets': 'Gustav Holst',
            'concierto de aranjuez': 'Joaquín Rodrigo',
            'aranjuez': 'Joaquín Rodrigo',
            '1812 overture': 'Pyotr Ilyich Tchaikovsky',
            'nutcracker': 'Pyotr Ilyich Tchaikovsky',
            'swan lake': 'Pyotr Ilyich Tchaikovsky',
            'sleeping beauty': 'Pyotr Ilyich Tchaikovsky'
        }
        
        if enriched_info.album_title:
            album_lower = enriched_info.album_title.lower()
            
            # Check for known works
            for work, composer in WORK_TO_COMPOSER.items():
                if work in album_lower:
                    return composer
            
            # Check album title for composer names
            for composer in self.CLASSICAL_COMPOSERS:
                if composer.lower() in album_lower:
                    return composer
                # Check last name only
                last_name = composer.split()[-1]
                if len(last_name) > 4 and last_name.lower() in album_lower:
                    return composer
        
        # Check for composer in "Composer: Work" pattern
        if enriched_info.album_title and ':' in enriched_info.album_title:
            potential_composer = enriched_info.album_title.split(':')[0].strip()
            canonical = ComposerAliases.get_canonical_name(potential_composer)
            if canonical in self.CLASSICAL_COMPOSERS:
                return canonical
        
        return None
    
    def _apply_quality_gates(self, enriched_info: EnrichedAlbumInfo, album_info: AlbumInfo,
                             top_category: str, sub_category: Optional[str]) -> Tuple[str, Optional[str]]:
        """Apply quality gates to correct misclassifications."""
        
        album_lower = enriched_info.album_title.lower() if enriched_info.album_title else ""
        artist_lower = enriched_info.artist.lower() if enriched_info.artist else ""
        
        # Quality Gate 1: Les Misérables MUST be in Soundtracks/Stage & Musicals
        if ('les misérables' in album_lower or 'les miserables' in album_lower):
            logger.info(f"Quality Gate: Moving Les Misérables to Soundtracks/Stage & Musicals")
            return "Soundtracks", "Stage & Musicals"
        
        # Quality Gate 2: Cirque du Soleil MUST be in Soundtracks/Stage & Musicals
        if 'cirque du soleil' in album_lower or 'cirque du soleil' in artist_lower:
            logger.info(f"Quality Gate: Moving Cirque du Soleil to Soundtracks/Stage & Musicals")
            return "Soundtracks", "Stage & Musicals"
        
        # Quality Gate 3: Disney musicals to Soundtracks
        disney_indicators = ['disney', 'aladdin', 'little mermaid', 'lion king', 
                            'beauty and the beast', 'frozen', 'moana', 'tangled']
        if any(term in album_lower for term in disney_indicators):
            if 'broadway' in album_lower or 'cast' in album_lower:
                logger.info(f"Quality Gate: Moving Disney musical to Soundtracks/Stage & Musicals")
                return "Soundtracks", "Stage & Musicals"
            else:
                logger.info(f"Quality Gate: Moving Disney to Soundtracks/Film")
                return "Soundtracks", "Film"
        
        # Quality Gate 4: Rock/Pop artists should NOT be in Classical
        pop_rock_artists = ['beach boys', 'emerson lake palmer', 'elp', 'yes', 'genesis',
                          'pink floyd', 'led zeppelin', 'queen', 'beatles', 'rolling stones',
                          'adele', 'santana', 'muse', 'dire straits', 'steely dan']
        if top_category == "Classical" and any(artist in artist_lower for artist in pop_rock_artists):
            logger.info(f"Quality Gate: Moving {enriched_info.artist} from Classical to Library")
            return "Library", None
        
        # Quality Gate 5: Studio Ghibli to Soundtracks
        if 'ghibli' in album_lower or 'totoro' in album_lower or 'mononoke' in album_lower:
            logger.info(f"Quality Gate: Moving Studio Ghibli to Soundtracks/Film")
            return "Soundtracks", "Film"
        
        # Quality Gate 6: Game soundtracks
        game_indicators = ['halo', 'zelda', 'mario', 'final fantasy', 'pokemon', 'nintendo']
        if any(game in album_lower for game in game_indicators):
            logger.info(f"Quality Gate: Moving game soundtrack to Soundtracks/Game")
            return "Soundtracks", "Game"
        
        # Quality Gate 7: The Cure albums should be in Library, not Soundtracks
        if 'the cure' in artist_lower or 'cure' == artist_lower:
            # Check if it's really their album, not a soundtrack
            cure_albums = ['staring at the sea', 'kiss me kiss me', 'seventeen seconds', 
                          'disintegration', 'pornography', 'head on the door']
            if any(album in album_lower for album in cure_albums) or top_category == "Soundtracks":
                logger.info(f"Quality Gate: Moving The Cure album to Library")
                return "Library", None
        
        # Quality Gate 8: Jazz artists wrongly in Soundtracks should move to Jazz
        jazz_artists = ['bill evans', 'miles davis', 'john coltrane', 'cannonball adderley',
                       'chet baker', 'sonny rollins', 'thelonious monk', 'art blakey',
                       'horace silver', 'kenny dorham', 'lee morgan', 'hank mobley',
                       'johnny coles', 'little johnny c']
        if top_category == "Soundtracks" and any(artist in artist_lower for artist in jazz_artists):
            # Check it's not really a soundtrack
            if not any(term in album_lower for term in ['soundtrack', 'ost', 'score', 'music from']):
                logger.info(f"Quality Gate: Moving jazz album to Jazz category")
                return "Jazz", None
        
        # Quality Gate 9: Classical works wrongly in Game
        if top_category == "Soundtracks" and sub_category == "Game":
            # Check for classical work patterns
            classical_patterns = ['sonata', 'concerto', 'symphony', 'quartet', 'quintet']
            if any(pattern in album_lower for pattern in classical_patterns):
                # Check if it's really a game soundtrack
                if not any(game in album_lower for game in ['zelda', 'halo', 'mario', 'final fantasy']):
                    logger.info(f"Quality Gate: Moving classical work from Game to Classical")
                    return "Classical", None
        
        # Quality Gate 10: Game of Thrones is TV, not Game
        if 'game of thrones' in album_lower:
            logger.info(f"Quality Gate: Moving Game of Thrones to Soundtracks/TV")
            return "Soundtracks", "TV"

        # Quality Gate: single-artist hits must stay with the artist, not Compilations
        if top_category == "Compilations & VA":
            solo_hits_artists = {'queen', 'tina turner', 'steely dan', 'dire straits'}
            if any(a in artist_lower for a in solo_hits_artists):
                logger.info("Quality Gate: Moving single-artist hits collection to Library")
                return "Library", None
        
        # Quality Gate 11: Mario Brunello (cellist) - Classical, not Game
        if 'mario brunello' in artist_lower and ('cello' in album_lower or 'sonata' in album_lower):
            logger.info(f"Quality Gate: Moving Mario Brunello cello work to Classical")
            return "Classical", None
        
        # Quality Gate 12: Irish/Celtic music might be miscategorized as Film
        celtic_indicators = ['kerry dancers', 'irish', 'celtic', 'gaelic']
        if top_category == "Soundtracks" and any(term in album_lower for term in celtic_indicators):
            # Unless it really is a soundtrack
            if not any(term in album_lower for term in ['soundtrack', 'ost', 'score', 'music from']):
                logger.info(f"Quality Gate: Moving Celtic/Irish music to Library")
                return "Library", None
        
        # Quality Gate 13: Albums with "Charade" might be jazz standards, not film
        if 'charade' in album_lower and top_category == "Soundtracks":
            # If it's Henry Mancini, it could be the actual soundtrack
            if 'mancini' not in artist_lower and 'soundtrack' not in album_lower:
                logger.info(f"Quality Gate: Moving Charade (likely jazz standard) to appropriate category")
                # Check if it's jazz
                if any(artist in artist_lower for artist in jazz_artists):
                    return "Jazz", None
                else:
                    return "Library", None
        
        # Quality Gate 14: "Film Music and Special Effects" is likely a demo/test disc
        if ('film music and special effects' in album_lower or
            re.search(r'\b(test cd|audiophile test|test disc|test\b)', album_lower) or
            re.search(r'\b(demo disc|demo cd|demo)\b', album_lower)):
            logger.info(f"Quality Gate: Moving test/demo disc to Compilations")
            return "Compilations & VA", None
        
        # Quality Gate 15: James Newton Howard albums - check if soundtrack or personal album
        if 'james newton howard' in artist_lower:
            # If it has "& Friends" or year in title without movie name, likely personal album
            if ('friends' in album_lower or 
                (enriched_info.year and str(enriched_info.year) in album_lower and 
                 not any(term in album_lower for term in ['soundtrack', 'score', 'ost']))):
                logger.info(f"Quality Gate: Moving James Newton Howard personal album to Library")
                return "Library", None
        
        # Quality Gate 16: My Neighbors the Yamadas is Studio Ghibli
        if 'yamadas' in album_lower or 'my neighbors the yamadas' in album_lower:
            logger.info(f"Quality Gate: Moving Yamadas (Studio Ghibli) to Soundtracks/Film")
            return "Soundtracks", "Film"

        # Post safety: Rock adaptations of classical (ELP Pictures…) => Library
        if 'emerson, lake & palmer' in artist_lower and 'pictures at an exhibition' in album_lower:
            logger.info("Quality Gate: Moving ELP 'Pictures at an Exhibition' to Library")
            return "Library", None
        
        return top_category, sub_category
    
    def _generate_album_path_comprehensive(self, enriched_info: EnrichedAlbumInfo, album_info: AlbumInfo,
                                          top_category: str, sub_category: Optional[str], 
                                          composer: Optional[str]) -> Path:
        """Generate the suggested organized album directory path with comprehensive rules."""
        
        # Start with music root (parent of album's current location)
        music_root = album_info.album_path.parents[len(album_info.parent_dirs)]
        
        # Build path components
        path_parts = [top_category]
        
        # Handle Classical composer-first organization
        if top_category == "Classical":
            if composer:
                # Single composer album: /Classical/{Composer}/{Work - Performers - YEAR [tags]}
                path_parts.append(composer)
                
                # Build album folder name with performers
                album_parts = []
                
                # Extract work title (remove composer name if present)
                work_title = enriched_info.album_title
                if composer.split()[-1].lower() in work_title.lower():
                    # Remove composer name from work title
                    work_title = re.sub(f"{re.escape(composer)}:?\\s*", "", work_title, flags=re.IGNORECASE).strip()
                
                album_parts.append(work_title)
                
                # Add performers if not the composer
                if enriched_info.artist and enriched_info.artist.lower() != composer.lower():
                    performer = self._normalize_performer_name(enriched_info.artist)
                    album_parts.append(performer)
                
                if enriched_info.year:
                    album_parts.append(str(enriched_info.year))
                
                # Add format tags
                format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
                if format_tags:
                    album_parts.append(' '.join(f"[{tag}]" for tag in format_tags))
                
                album_folder = " - ".join(album_parts)
            elif sub_category == "Recitals":
                # Recital: /Classical/Recitals/{Performer}/{Album - YEAR [tags]}
                path_parts.append("Recitals")
                performer = self._sanitize_filename(enriched_info.artist)
                path_parts.append(performer)
                
                album_parts = [enriched_info.album_title]
                if enriched_info.year:
                    album_parts.append(str(enriched_info.year))
                
                format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
                if format_tags:
                    album_parts.append(' '.join(f"[{tag}]" for tag in format_tags))
                
                album_folder = " - ".join(album_parts)
            else:
                # Generic classical
                album_folder = self._build_standard_album_folder(enriched_info, album_info)
        
        # Handle Soundtracks organization
        elif top_category == "Soundtracks":
            if sub_category:
                path_parts.append(sub_category)
            
            # Special clustering for Studio Ghibli
            album_lower = enriched_info.album_title.lower() if enriched_info.album_title else ""
            artist_lower = enriched_info.artist.lower() if enriched_info.artist else ""
            
            # More comprehensive Studio Ghibli film list
            ghibli_terms = [
                'ghibli', 'totoro', 'mononoke', 'spirited away', 'howl\'s moving castle',
                'howl', 'kiki', 'ponyo', 'arrietty', 'laputa', 'castle in the sky',
                'nausicaa', 'nausicaä', 'porco rosso', 'earthsea', 'whisper of the heart',
                'grave of the fireflies', 'pom poko', 'tanuki', 'the cat returns',
                'my neighbors the yamadas', 'yamadas', 'marnie', 'the wind rises',
                'princess mononoke', 'ocean waves', 'from up on poppy hill'
            ]

            # Include non-feature short "On Your Mark" (1995)
            ghibli_terms.append('on your mark')
            
            # Check both album title and artist (Joe Hisaishi often does Ghibli)
            if (any(term in album_lower for term in ghibli_terms) or
                ('hisaishi' in artist_lower and any(term in album_lower for term in ['my neighbor', 'castle', 'princess']))):
                path_parts.append("Studio Ghibli")
                # Use the album title as the folder name
                album_folder = enriched_info.album_title
                # Add year and format tags if available
                if enriched_info.year:
                    album_folder += f" - {enriched_info.year}"
                format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
                if format_tags:
                    album_folder += " " + ' '.join(f"[{tag}]" for tag in format_tags)
            
            # Special clustering for Les Misérables versions
            elif 'les misérables' in album_lower or 'les miserables' in album_lower:
                path_parts.append("Les Misérables")
                # Add descriptive version name
                if '1987' in album_lower or 'original broadway' in album_lower:
                    album_folder = "1987 Original Broadway Cast"
                elif '1988' in album_lower or 'symphonic' in album_lower:
                    album_folder = "1988 Complete Symphonic Recording"
                elif '1996' in album_lower or 'royal albert' in album_lower:
                    album_folder = "1996 10th Anniversary - Royal Albert Hall"
                elif '2010' in album_lower or '25th' in album_lower:
                    album_folder = "2010 25th Anniversary UK Tour Cast"
                elif '2012' in album_lower:
                    album_folder = "2012 Film Soundtrack"
                elif '2020' in album_lower or 'staged concert' in album_lower:
                    album_folder = "2020 Staged Concert"
                else:
                    album_folder = enriched_info.album_title
                
                # Add year and format tags if not already in folder name
                if enriched_info.year and str(enriched_info.year) not in album_folder:
                    album_folder += f" - {enriched_info.year}"
                format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
                if format_tags:
                    album_folder += " " + ' '.join(f"[{tag}]" for tag in format_tags)
            else:
                # Standard soundtrack organization
                album_parts = [enriched_info.album_title]
                if enriched_info.year:
                    album_parts.append(str(enriched_info.year))
                
                format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
                if format_tags:
                    album_parts.append(' '.join(f"[{tag}]" for tag in format_tags))
                
                album_folder = " - ".join(album_parts)
        
        # Handle Compilations & VA with series detection
        elif top_category == "Compilations & VA":
            series_name = self._detect_series_name(enriched_info.album_title)
            if series_name:
                path_parts.append(series_name)
                # Extract volume/part from album title
                volume = self._extract_volume(enriched_info.album_title, series_name)
                album_folder = volume if volume else enriched_info.album_title
            else:
                album_folder = self._build_standard_album_folder(enriched_info, album_info)
        
        # Handle standard categories (Library, Jazz, Electronic)
        else:
            # Always add artist folder for Library/Jazz/Electronic categories
            # These categories should only contain single-artist albums
            # (true compilations go to "Compilations & VA")
            if enriched_info.artist and enriched_info.artist != "Unknown Artist":
                artist_folder = self._sanitize_filename(enriched_info.artist)
                path_parts.append(artist_folder)
            
            album_folder = self._build_standard_album_folder(enriched_info, album_info)
        
        album_folder = self._sanitize_filename(album_folder)
        path_parts.append(album_folder)
        
        return music_root / Path(*path_parts)
    
    def _normalize_performer_name(self, performer: str) -> str:
        """Normalize performer name for classical albums."""
        # Apply orchestra aliases
        performer = OrchestraAliases.get_canonical_name(performer)
        
        # Clean up conductor/orchestra formatting
        performer = performer.replace(' & ', ' & ')
        performer = re.sub(r'\s+', ' ', performer)
        
        return performer
    
    def _detect_series_name(self, album_title: str) -> Optional[str]:
        """Detect if album belongs to a series."""
        series_patterns = {
            'Best Audiophile Voices': r'best audiophile voices',
            'Audiophile Reference': r'audiophile reference',
            'Super Analog Sound': r'super analog sound',
            'The Best Of': r'the best of\s+\w+',
            'The Essential Collection': r'the essential collection',
            'The Complete Mike Oldfield': r'the complete mike oldfield',
            'Super Sound': r'super sound\s*(vol|volume)?',
            'Three Blind Mice': r'(three blind mice|tbm|the super .* sound of tbm)',
            'The Best Songs Of The World': r'the best songs of the world',
            'Max Mix': r'max mix',
            'JVC XRCD': r'jvc xrcd\d*\s*(sampler|audiophile|collection)',
            'XRCD Sampler': r'xrcd\d*\s*sampler',
        }
        
        album_lower = album_title.lower()
        for series_name, pattern in series_patterns.items():
            if re.search(pattern, album_lower):
                return series_name
        
        return None
    
    def _extract_volume(self, album_title: str, series_name: str) -> Optional[str]:
        """Extract volume/part number from album title."""
        # Look for volume patterns
        patterns = [
            r'[Vv]ol(?:ume)?\.?\s*(\d+|[IVX]+)',
            r'[Pp]art\s*(\d+|[IVX]+)',
            r'(\d+|[IVX]+)\s*$',  # Number at end
        ]
        
        for pattern in patterns:
            match = re.search(pattern, album_title)
            if match:
                return f"Volume {match.group(1)}"
        
        # Return cleaned album title without series name
        cleaned = re.sub(re.escape(series_name), '', album_title, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+', ' ', cleaned.strip())
        return cleaned if cleaned else None
    
    def _build_standard_album_folder(self, enriched_info: EnrichedAlbumInfo, 
                                    album_info: AlbumInfo) -> str:
        """Build standard album folder name."""
        # Translate CJK characters if present
        album_title = self._translate_cjk_if_needed(enriched_info.album_title)
        
        album_parts = [album_title]
        
        if enriched_info.year:
            album_parts.append(str(enriched_info.year))
        
        format_tags = self._extract_format_tags(album_info.album_name, enriched_info.album_title)
        if format_tags:
            album_parts.append(' '.join(f"[{tag}]" for tag in format_tags))
        
        return " - ".join(album_parts)
    
    def _translate_cjk_if_needed(self, text: str) -> str:
        """Translate CJK text to romanized form with original in parentheses."""
        if not text:
            return text
        
        # Check if text contains CJK characters
        has_cjk = any(
            '\u4e00' <= char <= '\u9fff' or  # Chinese
            '\u3040' <= char <= '\u309f' or  # Hiragana
            '\u30a0' <= char <= '\u30ff' or  # Katakana
            '\uac00' <= char <= '\ud7af'     # Korean
            for char in text
        )
        
        if not has_cjk:
            return text
        
        # For now, return original text
        # TODO: In future, use LLM to translate
        # Example format: "Liu Hansheng List (刘汉盛榜单) NO.100 Chang Xiang (畅响)"
        return text
    
    def _build_processing_notes(self, enriched_info: EnrichedAlbumInfo, 
                               top_category: str, sub_category: Optional[str],
                               composer: Optional[str]) -> List[str]:
        """Build processing notes for the album."""
        notes = [f"Processed as complete album ({enriched_info.total_tracks} tracks)"]
        
        if composer:
            notes.append(f"Classical composer-first organization: {composer}")
        
        if sub_category:
            notes.append(f"Sub-category: {sub_category}")
        
        if enriched_info.is_compilation:
            notes.append("Identified as compilation/various artists")
        
        if enriched_info.disc_count and enriched_info.disc_count > 1:
            notes.append(f"Multi-disc album: {enriched_info.disc_count} discs")
        
        return notes
    
    def _canonicalize_artist(self, artist: str) -> str:
        """Clean and normalize artist name."""
        # Apply canonical names (check all alias types)
        artist = ArtistAliases.get_canonical_name(artist)
        artist = ComposerAliases.get_canonical_name(artist)
        artist = OrchestraAliases.get_canonical_name(artist)
        
        # Clean spacing
        artist = re.sub(r'\s+', ' ', artist.strip())
        
        # Fix capitalization if needed
        if artist.islower() or artist.isupper():
            artist = artist.title()
        
        return artist
    
    def _canonicalize_title(self, title: str) -> str:
        """Clean and normalize album title."""
        # Remove format indicators
        title = re.sub(r'\[(FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD|MFSL|24-\d+|SHM-CD)\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\((FLAC|MP3|WAV|ALAC|XRCD|K2HD|SACD|DSD|MFSL|24-\d+|SHM-CD)\)', '', title, flags=re.IGNORECASE)
        
        # Clean underscores and normalize spacing
        title = title.replace('_', ' ')
        title = re.sub(r'\s+', ' ', title.strip())
        
        return title
    
    def _extract_format_tags(self, album_name: str, album_title: str) -> List[str]:
        """Extract format tags from album folder name or title."""
        text = f"{album_name} {album_title}"
        
        format_patterns = {
            'XRCD24': r'\bXRCD24\b',
            'XRCD': r'\bXRCD\b',
            'K2HD': r'\bK2HD\b',
            'SHM-CD': r'\bSHM-?CD\b',
            'MFSL': r'\b(MFSL|Mobile Fidelity)\b',
            'SACD': r'\bSACD\b',
            'DSD': r'\bDSD\b',
            '24-96': r'\b24[-/]96\b',
            '24-88': r'\b24[-/]88\b',
            '24-192': r'\b24[-/]192\b'
        }
        
        found_tags = []
        for tag, pattern in format_patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                if tag not in found_tags:  # Avoid duplicates
                    found_tags.append(tag)

        # Normalize order and uniqueness
        return sorted(set(found_tags))

    # --- Safety Nets -------------------------------------------------------
    # Iconic pop/rock and jazz artists to prevent misroutes
    POP_ROCK_LIBRARY = {
        'a-ha', 'aha', 'duran duran', 'mecano', 'muse', 'queen', 'tina turner',
        'steely dan', 'dire straits', 'adele', 'beach boys', 'emerson, lake & palmer',
        'ani difranco', 'book of love'
    }

    JAZZ_SAFETY = {
        'bill evans', 'miles davis', 'john coltrane', 'cannonball adderley', 'chet baker',
        'sonny rollins', 'thelonious monk', 'art blakey', 'horace silver', 'kenny dorham',
        'lee morgan', 'hank mobley', 'gerry mulligan', 'barney kessel', 'ben webster',
        'red garland', 'winton kelly', 'tsuyoshi yamamoto', 'arne domnérus', 'arne domnerus',
        'art pepper'
    }

    def _safety_net_pre(self, genres_lower: List[str], artist_lower: str, album_lower: str):
        # If artist is iconic pop/rock => Library
        if any(a in artist_lower for a in self.POP_ROCK_LIBRARY):
            return ("Library", None, None)
        # If unmistakably jazz artist => Jazz
        if any(a in artist_lower for a in self.JAZZ_SAFETY):
            return ("Jazz", None, None)
        return None

    def _safety_net_post(self, top_category: str, sub_category: Optional[str], artist_lower: str, album_lower: str):
        # Rock adaptations of classical (ELP Pictures…) => Library
        if 'emerson, lake & palmer' in artist_lower and 'pictures at an exhibition' in album_lower:
            return "Library", None
        return top_category, sub_category
    
    def _sanitize_filename(self, filename: str, max_length: int = 200) -> str:
        """Sanitize filename for cross-platform compatibility."""
        # Remove invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Remove control characters
        filename = ''.join(char for char in filename if ord(char) >= 32)
        
        # Normalize whitespace
        filename = ' '.join(filename.split())
        
        # Remove leading/trailing dots and spaces
        filename = filename.strip(' .')
        
        if not filename:
            filename = "unknown_album"
        
        # Truncate if too long
        if len(filename) > max_length:
            filename = filename[:max_length-4] + "..."
        
        return filename
