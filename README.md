# tornettools

![](https://github.com/shadow/tornettools/workflows/Build/badge.svg)

This tool generates configuration files that can be used to set up and run
private Tor networks of a configurable scale. The configuration files that
are generated can be run in the
[Shadow network simulator](https://github.com/shadow/shadow);
[NetMirage](https://crysp.uwaterloo.ca/software/netmirage)
and
[Chutney](https://gitweb.torproject.org/chutney.git)
may eventually support the files generated with this tool.

The generated networks include the use of
[TGen](https://github.com/shadow/tgen)
for the generation of realistic background traffic, and
[OnionTrace](https://github.com/shadow/oniontrace)
for the collection of information from Tor throughout an experiment.

### setup is easy with virtualenv and pip

    virtualenv -p /bin/python3 tornettoolsenv
    source tornettoolsenv/bin/activate
    pip install -r requirements.txt
    pip install -I .

### read the help menus

    tornettools -h
    tornettools stage -h
    tornettools generate -h

### grab the data we need

    wget https://collector.torproject.org/archive/relay-descriptors/consensuses/consensuses-2020-01.tar.xz
    wget https://collector.torproject.org/archive/relay-descriptors/server-descriptors/server-descriptors-2020-01.tar.xz
    wget https://metrics.torproject.org/userstats-relay-country.csv
    wget https://collector.torproject.org/archive/torperf/torperf-2020-01.tar.xz
    wget -O bandwidth-2020-01.csv "https://metrics.torproject.org/bandwidth.csv?start=2020-01-01&end=2020-01-31"

### extract

    tar xaf consensuses-2020-01.tar.xz
    tar xaf server-descriptors-2020-01.tar.xz
    tar xaf torperf-2020-01.tar.xz

### we also utilize privcount Tor traffic model measurements

    git clone https://github.com/tmodel-ccs2018/tmodel-ccs2018.github.io.git

### we also need tor

    sudo apt-get install openssl openssl-dev libevent libevent-dev
    git clone https://git.torproject.org/tor.git
    cd tor
    ./autogen.sh
    ./configure --disable-asciidoc --disable-unittests --disable-manpage --disable-html-manual
    make
    cd ..

### in order to generate, we need a tor and tor-gencert binaries (to generate relay keys)

    export PATH=${PATH}:`pwd`/tor/src/core/or:`pwd`/tor/src/app:`pwd`/tor/src/tools

### stage first, process relay and user info

    tornettools stage consensuses-2020-01 server-descriptors-2020-01 userstats-relay-country.csv --geoip_path tor/src/config/geoip

### now we can used the staged files to generate many times

For example, use '-n 0.1' to generate a private Tor network at '10%' the scale of public Tor:

    tornettools generate relayinfo_staging_2020-01-01--2020-02-01.json userinfo_staging_2020-01-01--2020-02-01.json tmodel-ccs2018.github.io --network_scale 0.1 --prefix tornet-0.1

### you can parse the torperf data so we can compare public Tor and our private Tor performance benchmarks

    tornettools parseperf torperf-2020-01

### now if you have shadow, tgen, and oniontrace installed, you can run shadow

    cd tornet-0.1
    shadow -w 12 shadow.config.xml > shadow.log
