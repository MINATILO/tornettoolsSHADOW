import os
import json
import logging
import subprocess
import shlex
import shutil

from multiprocessing import Pool, cpu_count

from numpy import array_split
from numpy.random import choice

from shadowtortools.generate_defaults import *

def __generate_authority_keys(torgencertexe, datadir, torrc, pwpath):
    cmd = "{} --create-identity-key -m 24 --passphrase-fd 0".format(torgencertexe)
    with open(pwpath, 'r') as pwin:
        retcode = subprocess.call(shlex.split(cmd), stdin=pwin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if retcode != 0:
        logging.critical("Error generating authority identity key using command line '{}'".format(cmd))
    assert retcode == 0

    v3ident = ""
    with open("authority_certificate", 'r') as certf:
        for line in certf:
            if 'fingerprint' in line:
                v3ident = line.strip().split()[1]

    shutil.move("authority_certificate", "{}/keys".format(datadir))
    shutil.move("authority_identity_key", "{}/keys".format(datadir))
    shutil.move("authority_signing_key", "{}/keys".format(datadir))

    return v3ident

def __generate_fingerprint(subproc_args):
    torexe, datadir, nickname, torrc = subproc_args
    listfp_cmd = "{} --list-fingerprint --DataDirectory {} --Nickname {} -f {}".format(torexe, datadir, nickname, torrc)
    retcode = subprocess.call(shlex.split(listfp_cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return [listfp_cmd, retcode]

def __read_fingerprint(datadir):
    with open("{0}/fingerprint".format(datadir), 'r') as f:
        shadowtor_fp = f.readline().strip().split()[1]
    return shadowtor_fp

def generate_tor_keys(args, relays):
    template_prefix = "{}/{}".format(args.prefix, SHADOW_TEMPLATE_PATH)
    hosts_prefix = "{}/{}".format(template_prefix, SHADOW_HOSTS_PATH)
    keygen_torrc = "{}/keygen.torrc".format(template_prefix)
    keygen_pw = "{}/keygen.pw".format(template_prefix)

    # create directories that do not exist
    if not os.path.exists(template_prefix):
        os.makedirs(template_prefix)
    if not os.path.exists(hosts_prefix):
        os.makedirs(hosts_prefix)

    # tor key generation configs
    print("DirServer test 127.0.0.1:5000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000\nORPort 5000\n",
        file=open(keygen_torrc, 'w'))
    print("shadowprivatenetwork\n", file=open(keygen_pw, 'w'))

    # generate the list of commands we need to run to generate the fingerprints
    work = []

    # handle authorities, we need at least 3 to produce valid consensus
    n_authorities = max(3, round(10.0 * args.network_scale))
    for i in range(n_authorities):
        nickname = "4uthority{}".format(i+1)
        datadir = "{}/{}".format(hosts_prefix, nickname)
        subproc_args = [args.torexe, datadir, nickname, keygen_torrc]
        work.append(subproc_args)

    # handle relays
    n_relays = 0
    for pos in ['g', 'e', 'ge', 'm']:
        for fp in relays[pos]:
            n_relays += 1
            nickname = relays[pos][fp]["nickname"]
            datadir = "{}/{}".format(hosts_prefix, nickname)
            subproc_args = [args.torexe, datadir, nickname, keygen_torrc]
            work.append(subproc_args)

    # run the fingerprint generator
    num_processes = args.nprocesses if args.nprocesses > 0 else cpu_count()
    results = []

    if num_processes > 1:
        # generate keys in parallel
        with Pool(processes=num_processes) as pool:
            results = pool.map(__generate_fingerprint, work)
    else:
        # generate keys synchronously
        for subproc_args in work:
            results.append(__generate_fingerprint(subproc_args))

    # make sure they all succeeded
    logging.info("Generated fingerprints and keys for {} Tor nodes ({} authorities and {} relays)".format(len(results), n_authorities, n_relays))
    for r in results:
        cmd, retcode = r
        if retcode != 0:
            logging.critical("Error generating fingerprint using command line '{}'".format(cmd))
        assert retcode == 0

    # read, parse, and store the resulting fingerprint
    for pos in ['g', 'e', 'ge', 'm']:
        for fp in relays[pos]:
            nickname = relays[pos][fp]["nickname"]
            datadir = "{}/{}".format(hosts_prefix, nickname)
            relays[pos][fp]["shadowtor_fingerprint"] = __read_fingerprint(datadir)

    authorities = {}
    for i in range(n_authorities):
        nickname = "4uthority{}".format(i+1)
        datadir = "{}/{}".format(hosts_prefix, nickname)
        fp = __read_fingerprint(datadir)
        authorities[fp] = {
            "nickname": nickname,
            "shadowtor_fingerprint": fp,
            "v3identity": __generate_authority_keys(args.torgencertexe, datadir, keygen_torrc, keygen_pw),
            "bandwidth_capacity": BW_1GBIT_BYTES,
            "address": "100.0.0.{0}".format(i+1),
            "country_code": choice(DIRAUTH_COUNTRY_CODES),
        }

    if os.path.exists(keygen_torrc):
        os.remove(keygen_torrc)
    if os.path.exists(keygen_pw):
        os.remove(keygen_pw)

    return authorities, relays

def generate_tor_config(args, authorities, relays):
    # make sure the config directory exists
    abs_conf_path = "{}/{}".format(args.prefix, CONFIG_DIRPATH)
    if not os.path.exists(abs_conf_path):
        os.makedirs(abs_conf_path)

    __generate_resolv_file(args, abs_conf_path)
    __generate_tor_v3bw_file(args, authorities, relays)
    __generate_torrc_common(abs_conf_path, authorities)
    __generate_torrc_authority(abs_conf_path, relays)
    __generate_torrc_exit(abs_conf_path)
    __generate_torrc_nonexit(abs_conf_path)
    __generate_torrc_markovclient(abs_conf_path)
    __generate_torrc_perfclient(abs_conf_path)

def __generate_resolv_file(args, conf_path):
    with open("{}/{}".format(conf_path, RESOLV_FILENAME), "w") as resolvfile:
        resolvfile.write("nameserver 127.0.0.1\n")

def __generate_tor_v3bw_file(args, authorities, relays):
    bwauth_dir = "{}/{}/{}/{}".format(args.prefix, SHADOW_TEMPLATE_PATH, SHADOW_HOSTS_PATH, BW_AUTHORITY_NAME)
    if not os.path.exists(bwauth_dir):
        os.makedirs(bwauth_dir)

    v3bw_init_path = "{}/v3bw.init.consensus".format(bwauth_dir)
    with open(v3bw_init_path, 'w') as v3bwfile:
        v3bwfile.write("946684801\n")

        # first get the minimum weight across all relays
        min_weight = 1.0
        for pos in ['ge', 'e', 'g', 'm']:
            sorted_relay_items = sorted(relays[pos].items(), key=lambda kv: kv[1]['weight'])
            (fp, relay) = sorted_relay_items[0]
            min_weight = min(min_weight, relay['weight'])

        for (fp, authority) in sorted(authorities.items(), key=lambda kv: kv[1]['nickname']):
            # authorities are weighted minimially for regular circuits
            cons_bw_weight = int(round(1.0))
            nickname = authority['nickname']
            shadowtor_fp = authority['shadowtor_fingerprint']
            v3bwfile.write("node_id=${}\tbw={}\tnick={}\n".format(shadowtor_fp, cons_bw_weight, nickname))

        for pos in ['ge', 'e', 'g', 'm']:
            # use reverse to sort each class from fastest to slowest when assigning the id counter
            for (fp, relay) in sorted(relays[pos].items(), key=lambda kv: kv[1]['weight'], reverse=True):
                cons_bw_weight = int(round(relay['weight']/min_weight))
                nickname = relay['nickname']
                shadowtor_fp = relay['shadowtor_fingerprint']
                v3bwfile.write("node_id=${}\tbw={}\tnick={}\n".format(shadowtor_fp, cons_bw_weight, nickname))

    # link to the initial v3bw file in the same directory
    v3bw_path = "{}/v3bw".format(bwauth_dir)
    os.symlink("v3bw.init.consensus", v3bw_path)

def __generate_torrc_common(conf_path, authorities):
    auth_names = []

    torrc_file = open("{}/{}".format(conf_path, TORRC_COMMON_FILENAME), 'w')

    for (fp, authority) in sorted(authorities.items(), key=lambda kv: kv[1]['nickname']):
        nickname = authority['nickname']
        v3id = authority['v3identity']
        address = authority['address']
        shadowtor_fp = authority['shadowtor_fingerprint']
        fp_with_spaces = " ".join(shadowtor_fp[i:i+4] for i in range(0, len(shadowtor_fp), 4))

        line = 'DirServer {} v3ident={} orport={} {}:{} {}'.format(nickname, v3id, TOR_OR_PORT, address, TOR_DIR_PORT, fp_with_spaces)
        torrc_file.write('{}\n'.format(line))

        auth_names.append(nickname)

    torrc_file.write('TestingTorNetwork 1\n')
    torrc_file.write('ServerDNSResolvConfFile {}/{}\n'.format(CONFIG_DIRPATH, RESOLV_FILENAME))
    torrc_file.write('ServerDNSTestAddresses {}\n'.format(','.join(auth_names)))
    torrc_file.write('ServerDNSAllowBrokenConfig 1\n')
    torrc_file.write('ServerDNSDetectHijacking 0\n')
    torrc_file.write('NumCPUs 1\n')
    torrc_file.write('Log notice stdout\n')
    torrc_file.write('SafeLogging 0\n')
    torrc_file.write('LogTimeGranularity 1\n')
    torrc_file.write('ContactInfo https://github.com/shadow/shadow-plugin-tor/issues\n')
    torrc_file.write('DisableDebuggerAttachment 0\n')
    torrc_file.write('CellStatistics 0\n')
    torrc_file.write('PaddingStatistics 0\n')
    torrc_file.write('DirReqStatistics 0\n')
    torrc_file.write('EntryStatistics 0\n')
    torrc_file.write('ExitPortStatistics 0\n')
    torrc_file.write('ConnDirectionStatistics 0\n')
    torrc_file.write('HiddenServiceStatistics 0\n')
    torrc_file.write('ExtraInfoStatistics 0\n')
    torrc_file.write('CircuitPriorityHalflife 30\n')
    torrc_file.write('PathBiasUseThreshold 10000\n')
    torrc_file.write('PathBiasCircThreshold 10000\n')
    torrc_file.write('DoSCircuitCreationEnabled 0\n')
    torrc_file.write('DoSConnectionEnabled 0\n')
    torrc_file.write('DoSRefuseSingleHopClientRendezvous 0\n')
    torrc_file.write('ControlPort {}\n'.format(TOR_CONTROL_PORT))

    torrc_file.close()

def __generate_torrc_authority(conf_path, relays):
    shadowtor_fps_g = [relays['g'][fp]['shadowtor_fingerprint'] for fp in relays['g']]
    shadowtor_fps_e = [relays['e'][fp]['shadowtor_fingerprint'] for fp in relays['e']]
    shadowtor_fps_ge = [relays['ge'][fp]['shadowtor_fingerprint'] for fp in relays['ge']]

    guard_fps = shadowtor_fps_g + shadowtor_fps_ge
    exit_fps = shadowtor_fps_e + shadowtor_fps_ge

    torrc_file = open("{}/{}".format(conf_path, TORRC_AUTHORITY_FILENAME), 'w')

    torrc_file.write('ORPort {}\n'.format(TOR_OR_PORT))
    torrc_file.write('DirPort {}\n'.format(TOR_DIR_PORT))
    torrc_file.write('SocksPort 0\n')
    torrc_file.write('Log info stdout\n')
    torrc_file.write('ExitPolicy "reject *:*"\n')
    torrc_file.write('\n')
    torrc_file.write('AuthoritativeDirectory 1\n')
    torrc_file.write('V3AuthoritativeDirectory 1\n')
    torrc_file.write('V3BandwidthsFile {}/{}/{}/v3bw\n'.format(SHADOW_DATA_PATH, SHADOW_HOSTS_PATH, BW_AUTHORITY_NAME))
    torrc_file.write('\n')
    torrc_file.write('TestingDirAuthVoteGuard {}\n'.format(','.join(guard_fps)))
    torrc_file.write('TestingDirAuthVoteGuardIsStrict 1\n')
    torrc_file.write('TestingDirAuthVoteExit {}\n'.format(','.join(exit_fps)))
    torrc_file.write('TestingDirAuthVoteExitIsStrict 1\n')

    torrc_file.close()

def __generate_torrc_exit(conf_path):
    torrc_file = open("{}/{}".format(conf_path, TORRC_EXITRELAY_FILENAME), 'w')

    torrc_file.write('#Log info stdout\n')
    torrc_file.write('ORPort {}\n'.format(TOR_OR_PORT))
    torrc_file.write('DirPort {}\n'.format(TOR_DIR_PORT))
    torrc_file.write('SocksPort 0\n')
    torrc_file.write('ExitPolicy "accept *:*"\n')

    torrc_file.close()

def __generate_torrc_nonexit(conf_path):
    torrc_file = open("{}/{}".format(conf_path, TORRC_NONEXITRELAY_FILENAME), 'w')

    torrc_file.write('#Log info stdout\n')
    torrc_file.write('ORPort {}\n'.format(TOR_OR_PORT))
    torrc_file.write('DirPort {}\n'.format(TOR_DIR_PORT))
    torrc_file.write('SocksPort 0\n')
    torrc_file.write('ExitPolicy "reject *:*"\n')

    torrc_file.close()

def __generate_torrc_markovclient(conf_path):
    torrc_file = open("{}/{}".format(conf_path, TORRC_MARKOVCLIENT_FILENAME), 'w')

    torrc_file.write('ClientOnly 1\n')
    torrc_file.write('ORPort 0\n')
    torrc_file.write('DirPort 0\n')
    torrc_file.write('SocksPort {}\n'.format(TOR_SOCKS_PORT))
    torrc_file.write('UseEntryGuards 0\n')

    torrc_file.close()

def __generate_torrc_perfclient(conf_path):
    torrc_file = open("{}/{}".format(conf_path, TORRC_PERFCLIENT_FILENAME), 'w')

    torrc_file.write('ClientOnly 1\n')
    torrc_file.write('ORPort 0\n')
    torrc_file.write('DirPort 0\n')
    torrc_file.write('SocksPort {}\n'.format(TOR_SOCKS_PORT))
    torrc_file.write('UseEntryGuards 0\n')
    torrc_file.write('MaxCircuitDirtiness 10 seconds\n')

    torrc_file.close()

def get_relays(args):
    with open(args.relay_info_path, 'r') as infile:
        data = json.load(infile)

    relays = data['relays']
    stats = data['network_stats']

    # sample relays: take all relays that appeared in the input data, and select
    # a number that follows the median number of relays that are seen in a consensus.
    # this gives us the relays that would represent a full 100% Tor network
    sampled_relays, sampled_weights = __sample_relays(relays, stats['med_count_total'])

    # log some info
    n_relays = len(sampled_relays['all'])
    total_capacity = sum([relay['bandwidth_capacity'] for relay in sampled_relays['all'].values()])
    gbit = total_capacity*8.0/1000.0/1000.0/1000.0
    logging.info("A full Tor network has {} relays with total capacity of {} Gbit/s".format(n_relays, gbit))

    # compute ratios of nodes for each position
    pos_ratios = {
        'g': len(sampled_relays['g']) / len(sampled_relays['all']),
        'e': len(sampled_relays['e']) / len(sampled_relays['all']),
        'ge': len(sampled_relays['ge']) / len(sampled_relays['all']),
        'm': len(sampled_relays['m']) / len(sampled_relays['all']),
    }

    # Now that we have a "full" Tor network, scale it down to the requested scale.
    n_relays_scaled = round(n_relays * args.network_scale)
    chosen_relays, divergence = __choose_relays(n_relays_scaled, sampled_relays, sampled_weights, pos_ratios)

    relay_count = len(chosen_relays['g']) + len(chosen_relays['e']) + len(chosen_relays['ge']) + len(chosen_relays['m'])
    logging.info("Chose {} of {} relays using scale factor {}".format(relay_count, n_relays, args.network_scale))

    # name the chosen relays
    relay_ctr = 1
    for pos in ['ge', 'e', 'g', 'm']:
        suffix = 'guard' if pos == 'g' else 'exit' if pos == 'e' else 'exitguard' if pos == 'ge' else 'middle'
        # use reverse to sort each class from fastest to slowest when assigning the id counter
        for (fp, relay) in sorted(chosen_relays[pos].items(), key=lambda kv: kv[1]['weight'], reverse=True):
            relay['nickname'] = "relay{}{}".format(relay_ctr, suffix)
            relay_ctr += 1

    return chosen_relays, relay_count

def __sample_relays(relays, sample_size):
    # we need to make sure the relay ordering matches, so create a list of prints
    all_fingerprints = list(relays.keys())

    # pick relays weighted by their run frequency (uptime)
    # if it was not running long enough or has no bandwidth, it won't get selected
    run_freqs = []
    for fp in all_fingerprints:
        freq = float(relays[fp]['running_frequency'])
        weight = float(relays[fp]['weight'])
        if freq < RUN_FREQ_THRESH or weight == 0.0:
            freq = 0.0
        run_freqs.append(freq)
    # normalize
    run_freqs_normed = [freq/sum(run_freqs) for freq in run_freqs]
    sampled_fingerprints = list(choice(all_fingerprints, p=run_freqs_normed, replace=False, size=sample_size))

    # track the results
    sampled_relays = {'all':{}, 'g':{}, 'e':{}, 'ge':{}, 'm':{}}
    sampled_weights = {'all':0, 'g':0, 'e':0, 'ge':0, 'm':0}
    for fp in sampled_fingerprints:
        relay, weight = relays[fp], relays[fp]['weight']

        # track list of all relays
        sampled_relays['all'][fp] = relay
        sampled_weights['all'] += weight

        # track relays by position too
        if relays[fp]['guard_frequency'] >= POS_FLAG_THRESH and relays[fp]['exit_frequency'] >= POS_FLAG_THRESH:
            sampled_relays['ge'][fp] = relay
            sampled_weights['ge'] += weight
        elif relays[fp]['exit_frequency'] >= POS_FLAG_THRESH:
            sampled_relays['e'][fp] = relay
            sampled_weights['e'] += weight
        elif relays[fp]['guard_frequency'] >= POS_FLAG_THRESH:
            sampled_relays['g'][fp] = relay
            sampled_weights['g'] += weight
        else:
            sampled_relays['m'][fp] = relay
            sampled_weights['m'] += weight

    # normalize the weights
    sampled_weights['g'] /= sampled_weights['all']
    sampled_weights['e'] /= sampled_weights['all']
    sampled_weights['ge'] /= sampled_weights['all']
    sampled_weights['m'] /= sampled_weights['all']
    sampled_weights['all'] /= sampled_weights['all']

    return sampled_relays, sampled_weights

def __choose_relays(n_relays, sampled_relays, sampled_weights, pos_ratios):
    # sort the relays by bandwidth weight
    # returns (key, value) relay items, i.e., (fingerprint, relay_data_dict)
    g_items_sorted = sorted(sampled_relays['g'].items(), key=lambda kv: kv[1]['weight'])
    e_items_sorted = sorted(sampled_relays['e'].items(), key=lambda kv: kv[1]['weight'])
    ge_items_sorted = sorted(sampled_relays['ge'].items(), key=lambda kv: kv[1]['weight'])
    m_items_sorted = sorted(sampled_relays['m'].items(), key=lambda kv: kv[1]['weight'])

    # split into k bins
    g_bins = array_split(g_items_sorted, round(n_relays*pos_ratios['g']))
    e_bins = array_split(e_items_sorted, round(n_relays*pos_ratios['e']))
    ge_bins = array_split(ge_items_sorted, round(n_relays*pos_ratios['ge']))
    m_bins = array_split(m_items_sorted, round(n_relays*pos_ratios['m']))

    # get the index of the median relay in each bin
    g_bin_indices = [len(bin)//2 for bin in g_bins]
    e_bin_indices = [len(bin)//2 for bin in e_bins]
    ge_bin_indices = [len(bin)//2 for bin in ge_bins]
    m_bin_indices = [len(bin)//2 for bin in m_bins]

    while True:
        # get the fingerprint of the median relay in each bin
        g_fingerprints = [g_bins[i][g_bin_indices[i]][0] for i in range(len(g_bins))]
        e_fingerprints = [e_bins[i][e_bin_indices[i]][0] for i in range(len(e_bins))]
        ge_fingerprints = [ge_bins[i][ge_bin_indices[i]][0] for i in range(len(ge_bins))]
        m_fingerprints = [m_bins[i][m_bin_indices[i]][0] for i in range(len(m_bins))]

        # add up the weights
        g_weight = sum([sampled_relays['g'][fp]['weight'] for fp in g_fingerprints])
        e_weight = sum([sampled_relays['e'][fp]['weight'] for fp in e_fingerprints])
        ge_weight = sum([sampled_relays['ge'][fp]['weight'] for fp in ge_fingerprints])
        m_weight = sum([sampled_relays['m'][fp]['weight'] for fp in m_fingerprints])
        total_weight_before = g_weight+e_weight+ge_weight+m_weight

        # normalize the weights
        g_frac = g_weight/total_weight_before
        e_frac = e_weight/total_weight_before
        ge_frac = ge_weight/total_weight_before
        m_frac = m_weight/total_weight_before

        # compute distance between relay class selection probabilities
        divergence_g = (g_frac-sampled_weights['g'])
        divergence_e = (e_frac-sampled_weights['e'])
        divergence_ge = (ge_frac-sampled_weights['ge'])
        divergence_m = (m_frac-sampled_weights['m'])

        max_divergence = max([abs(divergence_ge), abs(divergence_e), abs(divergence_g), abs(divergence_m)])

        # At this point, we could go through the lists of indices to tweak them
        # in order to reduce the divergence between relay classes. But I haven't
        # found large divergence in practice, and the algorithm is hard to get right,
        # so I am not implementing it (yet).
        # The algorithm would be to go through all bins and for each index, check
        # index+1 or index-1 (depending on which side we need to balance), and find
        # the index change that would increase or decrease the weight the most without
        # causing a divergence in the opposite direction. In other words, we are trying
        # to minimize the number tweaks we need to make, so we prefer the index tweak
        # that gets us closest to the desired weight without going over.
        break

    # check that the relative position weights are close to those in the full network
    logging.info("{} relays: relative position weights:".format(n_relays))
    logging.info("g: chosen={}, target={}, diff={}".format(g_frac, sampled_weights['g'], divergence_g))
    logging.info("e: chosen={}, target={}, diff={}".format(e_frac, sampled_weights['e'], divergence_e))
    logging.info("ge: chosen={}, target={}, diff={}".format(ge_frac, sampled_weights['ge'], divergence_ge))
    logging.info("m: chosen={}, target={}, diff={}".format(m_frac, sampled_weights['m'], divergence_m))
    logging.info("The max weight divergence between positions is {}".format(max_divergence))

    chosen_relays = {
        'g':{fp: sampled_relays['g'][fp] for fp in g_fingerprints},
        'e':{fp: sampled_relays['e'][fp] for fp in e_fingerprints},
        'ge':{fp: sampled_relays['ge'][fp] for fp in ge_fingerprints},
        'm':{fp: sampled_relays['m'][fp] for fp in m_fingerprints}
    }

    # renormalize the weights for the scaled network
    logging.info("Renormalizing weights for scaled network using total weight={}".format(total_weight_before))
    total_weight_after = 0.0

    for pos in ['g', 'e', 'ge', 'm']:
        for fp in chosen_relays[pos]:
            chosen_relays[pos][fp]['weight'] /= total_weight_before
            total_weight_after += chosen_relays[pos][fp]['weight']

    assert round(total_weight_after) == 1.0

    return chosen_relays, max_divergence

# currently unused, but kept around for posterity
def __choose_relays_old(n_relays, sampled_relays, sampled_weights, pos_ratios):
    # choose relays using the median bucketing approach
    relays_g, weight_g = __choose_best_fit(sampled_relays['g'], int(n_relays*pos_ratios['g']))
    relays_e, weight_e = __choose_best_fit(sampled_relays['e'], int(n_relays*pos_ratios['e']))
    relays_ge, weight_ge = __choose_best_fit(sampled_relays['ge'], int(n_relays*pos_ratios['ge']))

    remaining = n_relays - len(relays_g) - len(relays_e) - len(relays_ge)
    relays_m, weight_m = __choose_best_fit(sampled_relays['m'], remaining)

    # normalize
    weight_total = weight_g + weight_e + weight_ge + weight_m
    weight_g /= weight_total
    weight_e /= weight_total
    weight_ge /= weight_total
    weight_m /= weight_total

    divergence_g = (weight_g-sampled_weights['g'])
    divergence_e = (weight_e-sampled_weights['e'])
    divergence_ge = (weight_ge-sampled_weights['ge'])
    divergence_m = (weight_m-sampled_weights['m'])
    max_divergence = max([abs(divergence_ge), abs(divergence_e), abs(divergence_g), abs(divergence_m)])

    chosen_relays = {
        'g':relays_g,
        'e':relays_e,
        'ge':relays_ge,
        'm':relays_m
    }

    return chosen_relays, max_divergence

# currently unused, but kept around for posterity
def __choose_best_fit(relays, k):
    """
    Sorts the relays by weight, splits into k bins,
    and then chooses the median element of each bin.
    Returns the list of k chosen relay fingerprints.
    """
    n = len(relays)
    if k >= n:
        logging.warning("requested {} relays, but only {} are available".format(k, n))
        k = n
    assert k <= n

    # sort the relays by bandwidth weight
    # returns (key, value) relay items, i.e., (fingerprint, relay_data_dict)
    sorted_relay_items = sorted(relays.items(), key=lambda kv: kv[1]['weight'])
    # split into k bins
    relay_bins = array_split(sorted_relay_items, k)
    # get the fingerprint of the median relay in each bin
    chosen_fingerprints = [bin[len(bin)//2][0] for bin in relay_bins]

    chosen_relays = {fp:relays[fp] for fp in chosen_fingerprints}
    chosen_weight = sum([relay['weight'] for relay in chosen_relays.values()])

    return chosen_relays, chosen_weight
