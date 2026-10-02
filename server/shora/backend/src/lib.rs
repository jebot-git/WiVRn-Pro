//! Native SlimeVR + HaritoraX + oscavmgr server with Shora frontends.
mod app;
mod autobone;
mod calibration;
mod config;
mod control;
mod face;
mod feeder;
mod haritorax;
mod logging;
mod reset;
mod skeleton;
mod settings;
mod smoothing;
mod solarxr;
mod status;
mod spatial_reference;
mod stay_aligned;
mod tracker;
mod ui;

use clap::Parser;

/// Entry point for the worker personality of the WiVRn executable.
pub fn run_worker(args: Vec<String>) -> anyhow::Result<()> {
    let cli = config::Cli::try_parse_from(args)?;
    let cfg = config::Config::load(&cli)?;
    logging::init(cfg.log_file.as_deref())?;
    tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build()?.block_on(app::run(cfg))
}
