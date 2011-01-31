'''
Created on Jan 5, 2011

@author: mkiyer
'''
import logging
import os
import sys
import subprocess
from optparse import OptionParser

# local imports
import pysam
import lib.config as config
from lib.config import JOB_SUCCESS, JOB_ERROR
from lib.base import check_executable, get_read_length, parse_library_type

from pipeline.align_full import align_pe_full, align_sr_full
from pipeline.align_segments import align, determine_read_segments
from pipeline.merge_read_pairs import merge_read_pairs
from pipeline.find_discordant_reads import find_discordant_reads
from pipeline.extend_sequences import extend_sequences
from pipeline.sort_discordant_reads import sort_discordant_reads
from pipeline.nominate_chimeras import nominate_chimeras
from pipeline.nominate_spanning_reads import nominate_spanning_reads
from pipeline.bedpe_to_fasta import bedpe_to_junction_fasta
from pipeline.merge_spanning_alignments import merge_spanning_alignments
from pipeline.profile_insert_size import profile_isize_stats
from pipeline.filter_chimeras import filter_chimeras

def check_command_line_args(options, args, parser):
    # check command line arguments
    if len(args) < 3:
        parser.error("Incorrect number of command line arguments")
    fastq_files = args[0:2]
    output_dir = args[2]
    # check that input fastq files exist
    read_lengths = []
    for mate,fastq_file in enumerate(fastq_files):
        if not os.path.isfile(args[0]):
            parser.error("mate '%d' fastq file '%s' is not valid" % 
                         (mate, fastq_file))
        logging.debug("Checking read length for file %s" % 
                      (fastq_file))
        read_lengths.append(get_read_length(fastq_file))
        logging.debug("Read length for file %s: %d" % 
                      (fastq_file, read_lengths[-1]))
    # check that mate read lengths are equal
    if len(set(read_lengths)) > 1:
        parser.error("read lengths mate1=%d and mate2=%d are unequal" % 
                     (read_lengths[0], read_lengths[1]))
    # check that seed length < read length
    if any(options.segment_length > rlen for rlen in read_lengths):
        parser.error("seed length %d cannot be longer than read length" % 
                     (options.segment_length))
    # check that output dir is not a regular file
    if os.path.exists(output_dir) and (not os.path.isdir(output_dir)):
        parser.error("Output directory name '%s' exists and is not a valid directory" % 
                     (output_dir))
    if check_executable(options.bowtie_build_bin):
        logging.debug("Checking for 'bowtie-build' binary... found")
    else:
        parser.error("bowtie-build binary not found or not executable")
    # check that bowtie program exists
    if check_executable(options.bowtie_bin):
        logging.debug("Checking for 'bowtie' binary... found")
    else:
        parser.error("bowtie binary not found or not executable")
    # check that alignment index exists
    if os.path.isdir(options.index_dir):
        logging.debug("Checking for chimerascan index directory... found")
    else:
        parser.error("chimerascan alignment index directory '%s' not valid" % 
                     (options.index_dir))
    # check that alignment index file exists
    align_index_file = os.path.join(options.index_dir, config.BOWTIE_INDEX_FILE)
    if os.path.isfile(align_index_file):
        logging.debug("Checking for bowtie index file... found")
    else:
        parser.error("chimerascan bowtie index file '%s' invalid" % (align_index_file))
    # check for sufficient processors
    if options.num_processors < config.BASE_PROCESSORS:
        logging.warning("Please specify >=2 processes using '-p' to allow program to run efficiently")

def up_to_date(outfile, infile):
    if not os.path.exists(infile):
        return False
    if not os.path.exists(outfile):
        return False
    if os.path.getsize(outfile) == 0:
        return False    
    return os.path.getmtime(outfile) >= os.path.getmtime(infile)

def write_default_isize_stats(filename, min_isize, max_isize):
    mean = int((max_isize - min_isize) / 2.0)
    pass

def main():
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    parser = OptionParser("usage: %prog [options] <mate1.fq> <mate2.fq> <output_dir>")
    parser.add_option("-p", "--processors", dest="num_processors", 
                      type="int", default=config.BASE_PROCESSORS)
    parser.add_option("--keep-tmp", dest="keep_tmp", action="store_true", 
                      default=False,
                      help="Do not delete intermediate files from run") 
    parser.add_option("--index", dest="index_dir",
                      help="Path to chimerascan index directory")
    parser.add_option("--bowtie-build-bin", dest="bowtie_build_bin", 
                      default="bowtie-build", 
                      help="Path to 'bowtie-build' program")
    parser.add_option("--bowtie-bin", dest="bowtie_bin", default="bowtie", 
                      help="Path to 'bowtie' program")
    parser.add_option("--bowtie-mode-v", action="store_true", 
                      dest="bowtie_mode_v", default=False,
                      help="Run bowtie with -v to ignore quality scores")
    parser.add_option("--multihits", type="int", dest="multihits", 
                      default=40)
    parser.add_option("--mismatches", type="int", dest="mismatches", 
                      default=2)
    parser.add_option("--segment-length", type="int", dest="segment_length", 
                      default=25)
    parser.add_option("--trim5", type="int", dest="trim5", 
                      default=0)
    parser.add_option("--trim3", type="int", dest="trim3", 
                      default=0)    
    parser.add_option("--quals", dest="fastq_format")
    parser.add_option("--min-fragment-length", type="int", 
                      dest="min_fragment_length", default=0,
                      help="Smallest expected fragment length")
    parser.add_option("--max-fragment-length", type="int", 
                      dest="max_fragment_length", default=1000,
                      help="Largest expected fragment length (reads less"
                      " than this fragment length are assumed to be "
                      " genomically contiguous) [default=%default]")
    parser.add_option("--max-indel-size", type="int", 
                      dest="max_indel_size", default=100,
                      help="Tolerate indels less than Nbp "
                      "[default=%default]", metavar="N")
    parser.add_option('--library', dest="library_type", default="fr")
    parser.add_option("--anchor-min", type="int", dest="anchor_min", default=0,
                      help="Minimum junction overlap required to call spanning reads")
    parser.add_option("--anchor-max", type="int", dest="anchor_max", default=5,
                      help="Junction overlap below which to enforce mismatch checks")
    parser.add_option("--anchor-mismatches", type="int", dest="anchor_mismatches", default=0,
                      help="Number of mismatches allowed within anchor region")
    options, args = parser.parse_args()
    check_command_line_args(options, args, parser)
    # extract command line arguments
    fastq_files = args[0:2]
    output_dir = args[2]
    # create output dir if it does not exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logging.info("Created output directory: %s" % (output_dir))
    # create log dir if it does not exist
    log_dir = os.path.join(output_dir, "log")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        logging.debug("Created directory for log files: %s" % (log_dir))        
    # gather and parse run parameters
    library_type = parse_library_type(options.library_type)    
    gene_feature_file = os.path.join(options.index_dir, config.GENE_FEATURE_FILE)
    bowtie_mode = "-v" if options.bowtie_mode_v else "-n"
    bowtie_index = os.path.join(options.index_dir, config.ALIGN_INDEX)
    original_read_length = get_read_length(fastq_files[0])
    # minimum fragment length cannot be smaller than the trimmed read length
    trimmed_read_length = original_read_length - options.trim5 - options.trim3
    min_fragment_length = max(options.min_fragment_length, 
                              trimmed_read_length)
    #
    # Initial Bowtie alignment step
    #
    # align in paired-end mode, trying to resolve as many reads as possible
    # this effectively rules out the vast majority of reads as candidate
    # fusions
    unaligned_fastq_param = os.path.join(output_dir, config.UNALIGNED_FASTQ_PARAM)
    maxmultimap_fastq_param = os.path.join(output_dir, config.MAXMULTIMAP_FASTQ_PARAM)
    aligned_bam_file = os.path.join(output_dir, config.ALIGNED_READS_BAM_FILE)
    if all(up_to_date(aligned_bam_file, fq) for fq in fastq_files):
        logging.info("[SKIPPED] Alignment results exist")
    else:    
        logging.info("Aligning full-length reads in paired-end mode")
        retcode = align_pe_full(fastq_files, 
                                bowtie_index,
                                aligned_bam_file, 
                                unaligned_fastq_param,
                                maxmultimap_fastq_param,
                                min_fragment_length=min_fragment_length,
                                max_fragment_length=options.max_fragment_length,
                                trim5=options.trim5,
                                trim3=options.trim3,
                                library_type=options.library_type,
                                num_processors=options.num_processors,
                                fastq_format=options.fastq_format,
                                multihits=options.multihits,
                                mismatches=options.mismatches,
                                bowtie_bin=options.bowtie_bin,
                                bowtie_mode=bowtie_mode)
        if retcode != 0:
            logging.error("Bowtie failed with error code %d" % (retcode))    
            sys.exit(retcode)
    #
    # Get insert size distribution
    #
    isize_stats_file = os.path.join(output_dir, config.ISIZE_STATS_FILE)
    if up_to_date(isize_stats_file, aligned_bam_file):
        logging.info("[SKIPPED] Profiling insert size distribution")
        f = open(isize_stats_file, "r")
        isize_stats = f.next().strip().split('\t')
        isize_mean, isize_median, isize_mode, isize_std = isize_stats         
        f.close()
    else:
        logging.info("Profiling insert size distribution")
        max_isize_samples = 1e6
        bamfh = pysam.Samfile(aligned_bam_file, "rb")
        isize_stats = profile_isize_stats(bamfh,
                                          min_fragment_length,
                                          options.max_fragment_length,
                                          max_samples=max_isize_samples)
        isize_mean, isize_median, isize_mode, isize_std = isize_stats
        bamfh.close()
        f = open(isize_stats_file, "w")
        print >>f, '\t'.join(map(str, [isize_mean, isize_median, isize_mode, isize_std]))
        f.close()    
    #
    # Discordant reads alignment step
    #
    discordant_bam_file = os.path.join(output_dir, config.DISCORDANT_BAM_FILE)
    unaligned_fastq_files = [os.path.join(output_dir, fq) for fq in config.UNALIGNED_FASTQ_FILES]
    # get the segments used in discordant alignment to know the effective
    # read length used to align.  we used this to set the 'padding' during
    # spanning read discovery
    segments = determine_read_segments(original_read_length, 
                                       segment_length=options.segment_length, 
                                       segment_trim=True, 
                                       trim5=options.trim5,
                                       trim3=options.trim3)
    segmented_read_length = segments[-1][1]
    logging.info("Segmented alignment will use effective read length of %d" % 
                 (segmented_read_length))
    if all(up_to_date(discordant_bam_file, fq) for fq in fastq_files):
        logging.info("[SKIPPED] Discordant alignment results exist")
    else:
        logging.info("Aligning initially unmapped reads in single read mode")
        align(unaligned_fastq_files, options.fastq_format, bowtie_index,
              discordant_bam_file, 
              bowtie_bin=options.bowtie_bin,
              num_processors=options.num_processors, 
              segment_length=options.segment_length,
              segment_trim=True,
              trim5=options.trim5, 
              trim3=options.trim3, 
              multihits=options.multihits,
              mismatches=options.mismatches, 
              bowtie_mode=bowtie_mode)
    #
    # Merge paired-end reads step
    #
    paired_bam_file = os.path.join(output_dir, config.DISCORDANT_PAIRED_BAM_FILE)
    if up_to_date(paired_bam_file, discordant_bam_file):
        logging.info("[SKIPPED] Read pairing results exist")
    else:
        logging.info("Pairing aligned reads")
        bamfh = pysam.Samfile(discordant_bam_file, "rb")
        paired_bamfh = pysam.Samfile(paired_bam_file, "wb", template=bamfh)
        merge_read_pairs(bamfh, paired_bamfh, 
                         options.min_fragment_length,
                         options.max_fragment_length,
                         library_type)
        paired_bamfh.close() 
        bamfh.close()
    #
    # Find discordant reads step
    #
    discordant_bedpe_file = os.path.join(output_dir, config.DISCORDANT_BEDPE_FILE)
    padding = original_read_length - segmented_read_length
    if (up_to_date(discordant_bedpe_file, paired_bam_file)):
        logging.info("[SKIPPED] Finding discordant reads")
    else:
        logging.info("Finding discordant reads")
        find_discordant_reads(pysam.Samfile(paired_bam_file, "rb"), 
                              discordant_bedpe_file, gene_feature_file,
                              max_indel_size=options.max_indel_size,
                              max_isize=options.max_fragment_length,
                              max_multihits=options.multihits,
                              library_type=library_type,
                              padding=padding)
    #
    # Extract full sequences of the discordant reads
    #
    extended_discordant_bedpe_file = os.path.join(output_dir, config.EXTENDED_DISCORDANT_BEDPE_FILE)
    if up_to_date(extended_discordant_bedpe_file, discordant_bedpe_file):
        logging.info("[SKIPPED] Retrieving full length sequences for realignment")
    else:
        logging.info("Retrieving full length sequences for realignment")
        extend_sequences(unaligned_fastq_files, 
                         discordant_bedpe_file,
                         extended_discordant_bedpe_file)
    #
    # Sort discordant reads
    #
    sorted_discordant_bedpe_file = os.path.join(output_dir, config.SORTED_DISCORDANT_BEDPE_FILE)
    if (up_to_date(sorted_discordant_bedpe_file, extended_discordant_bedpe_file)):
        logging.info("[SKIPPED] Sorting discordant BEDPE file")
    else:        
        logging.info("Sorting discordant BEDPE file")
        sort_discordant_reads(extended_discordant_bedpe_file, sorted_discordant_bedpe_file)        
    #
    # Nominate chimeras step
    #
    encompassing_bedpe_file = os.path.join(output_dir, config.ENCOMPASSING_CHIMERA_BEDPE_FILE)        
    if (up_to_date(encompassing_bedpe_file, sorted_discordant_bedpe_file)):
        logging.info("[SKIPPED] Nominating chimeras from discordant reads")
    else:        
        logging.info("Nominating chimeras from discordant reads")
        nominate_chimeras(open(sorted_discordant_bedpe_file, "r"),
                          open(encompassing_bedpe_file, "w"),
                          gene_feature_file,                          
                          trim=config.EXON_JUNCTION_TRIM_BP)
    #
    # Nominate spanning reads step
    #
    spanning_fastq_file = os.path.join(output_dir, config.SPANNING_FASTQ_FILE)
    if up_to_date(spanning_fastq_file, extended_discordant_bedpe_file):
        logging.info("[SKIPPED] Nominating junction spanning reads")
    else:
        logging.info("Nominating junction spanning reads")
        nominate_spanning_reads(open(extended_discordant_bedpe_file, 'r'),
                                open(encompassing_bedpe_file, 'r'),
                                open(spanning_fastq_file, 'w'))    
    #
    # Extract junction sequences from chimeras file
    #        
    ref_fasta_file = os.path.join(options.index_dir, config.ALIGN_INDEX + ".fa")
    junc_fasta_file = os.path.join(output_dir, config.JUNC_REF_FASTA_FILE)
    junc_map_file = os.path.join(output_dir, config.JUNC_REF_MAP_FILE)
    spanning_read_length = get_read_length(spanning_fastq_file)    
    if (up_to_date(junc_fasta_file, encompassing_bedpe_file) and
        up_to_date(junc_map_file, encompassing_bedpe_file)):        
        logging.info("[SKIPPED] Chimeric junction files exist")
    else:        
        logging.info("Extracting chimeric junction sequences")
        bedpe_to_junction_fasta(encompassing_bedpe_file, ref_fasta_file,                                
                                spanning_read_length, 
                                open(junc_fasta_file, "w"),
                                open(junc_map_file, "w"))
    #
    # Build a bowtie index to align and detect spanning reads
    #
    bowtie_spanning_index = os.path.join(output_dir, config.JUNC_BOWTIE_INDEX)
    bowtie_spanning_index_file = os.path.join(output_dir, config.JUNC_BOWTIE_INDEX_FILE)
    if (up_to_date(bowtie_spanning_index_file, junc_fasta_file)):
        logging.info("[SKIPPED] Bowtie junction index exists")
    else:        
        logging.info("Building bowtie index for junction-spanning reads")
        args = [options.bowtie_build_bin, junc_fasta_file, bowtie_spanning_index]
        f = open(os.path.join(log_dir, "bowtie_build.log"), "w")
        subprocess.call(args, stdout=f, stderr=f)
        f.close()
    #
    # Align unmapped reads across putative junctions
    #
    junc_bam_file = os.path.join(output_dir, config.JUNC_READS_BAM_FILE)
    if (up_to_date(junc_bam_file, bowtie_spanning_index_file) and
        up_to_date(junc_bam_file, spanning_fastq_file)):
        logging.info("[SKIPPED] Aligning junction spanning ")
    else:            
        logging.info("Aligning junction spanning reads")
        retcode = align_sr_full(spanning_fastq_file, 
                                bowtie_spanning_index,
                                junc_bam_file,
                                trim5=options.trim5,
                                trim3=options.trim3,                                 
                                num_processors=options.num_processors,
                                fastq_format=options.fastq_format,
                                multihits=options.multihits,
                                mismatches=options.mismatches,
                                bowtie_bin=options.bowtie_bin,
                                bowtie_mode=bowtie_mode)
        if retcode != 0:
            logging.error("Bowtie failed with error code %d" % (retcode))    
            sys.exit(retcode)   
#        align([spanning_fastq_file], 
#              options.fastq_format,
#              bowtie_spanning_index,
#              junc_bam_file, 
#              bowtie_bin=options.bowtie_bin, 
#              num_processors=options.num_processors, 
#              segment_length=options.segment_length,
#              segment_trim=False,
#              trim5=options.trim5, 
#              trim3=options.trim3, 
#              multihits=options.multihits,
#              mismatches=options.mismatches, 
#              bowtie_mode=bowtie_mode)
    #
    # Merge spanning and encompassing read information
    #
    raw_chimera_bedpe_file = os.path.join(output_dir, config.RAW_CHIMERA_BEDPE_FILE)
    if (up_to_date(raw_chimera_bedpe_file, junc_bam_file) and
        up_to_date(raw_chimera_bedpe_file, junc_map_file)):
        logging.info("[SKIPPED] Merging spanning and encompassing read alignments")
    else:
        logging.info("Merging spanning and encompassing read alignments")
        merge_spanning_alignments(junc_bam_file, junc_map_file, 
                                  raw_chimera_bedpe_file,
                                  spanning_read_length, 
                                  anchor_min=0, 
                                  anchor_max=0,
                                  anchor_mismatches=0)
    #
    # Apply final filters
    #
#    chimera_bedpe_file = os.path.join(output_dir, config.CHIMERA_BEDPE_FILE)
#    if (up_to_date(chimera_bedpe_file, raw_chimera_bedpe_file)):
#        logging.info("[SKIPPED] Filtering chimeras")
#    else:
#        logging.info("Filtering chimeras")
#        filter_chimeras(raw_chimera_bedpe_file, 
#                        chimera_bedpe_file,
#                        isoforms=False,
#                        overlap=False,
#                        max_isize=None,
#                        prob=0.05)            
    retcode = JOB_SUCCESS
    sys.exit(retcode)

if __name__ == '__main__':
    main()