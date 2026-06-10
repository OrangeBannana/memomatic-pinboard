/* spi_touch_read.c — ADS7846 coordinate reader via /dev/mem
 *
 * Waits for fbcp-ili9341's DMA to finish a frame (SPI TA: 1→0), then reads
 * one averaged X,Y sample from the ADS7846 in the inter-frame gap.
 *
 * Key fix: after the ads7846 kernel driver is unbound, GPIO7 (SPI0_CE1_N)
 * reverts from ALT0 to input mode.  The SPI hardware then cannot physically
 * assert CS1 so the ADS7846 is never selected and MISO reads back 0x00.
 * We re-apply ALT0 on the GPFSEL0 register before each read.
 *
 * All diagnostic output goes to stderr AFTER the SPI section is complete;
 * no I/O syscalls are made in the hot path between wait_interframe() and
 * the final SPI deassert, so we don't inadvertently miss the inter-frame gap.
 *
 * Outputs to stdout:
 *   "X Y\n"   (calibrated screen coordinates, 0-based)
 *   "err\n"   on failure
 *
 * Outputs to stderr (always):
 *   "ta_seen=0|1"              whether fbcp SPI frame was detected
 *   "sample0 ry=N rx=N ok|filtered"   raw ADC per sample
 *   "raw AY AX N"              averages and accepted-sample count
 *
 * Build:  gcc -O2 -o spi_touch_read spi_touch_read.c
 *
 * Calibration source: /etc/X11/xorg.conf.d/99-calibration.conf
 *   Option "Calibration" "3936 227 268 3880"   (phys-X range / phys-Y range)
 *   Option "SwapAxes"    "1"                   (physical X → screen Y, Y → X)
 * X11 display: 480×320  (confirmed: xdotool center = 240,160)
 */
#include <stdio.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

/* BCM2710 (Pi Zero 2 W / Pi 3) peripheral base */
#define PERI_BASE  0x3F000000UL
#define SPI0_PHYS  (PERI_BASE + 0x204000UL)
#define GPIO_PHYS  (PERI_BASE + 0x200000UL)
#define PAGE_SIZE  4096

/* SPI0 register word offsets */
#define SPI_CS   0
#define SPI_FIFO 1
#define SPI_CLK  2

/* CS register bits */
#define CS_CS1  0x01u
#define CS_TA   (1u << 7)
#define CS_CLR  0x30u
#define CS_DONE (1u << 16)
#define CS_RXD  (1u << 17)
#define CS_TXD  (1u << 18)

/* ADS7846 commands: 12-bit differential, PENIRQ re-enabled after conversion */
#define CMD_Y  0x90   /* physical Y axis → screen X */
#define CMD_X  0xD0   /* physical X axis → screen Y */

/* Calibration constants from /etc/X11/xorg.conf.d/99-calibration.conf */
#define CAL_Y_MIN   268
#define CAL_Y_MAX  3880
#define CAL_X_MIN  3936
#define CAL_X_MAX   227   /* < MIN — axis is inverted */
#define SCREEN_W    480
#define SCREEN_H    320

/* Accept ADC samples in this range; rail values (0/4095) mean pen is lifted */
#define RAW_MIN  50
#define RAW_MAX  4050

/* GPIO FSEL: GPIO7 = SPI0_CE1_N, bits [23:21] of GPFSEL0, ALT0 = 0b100 */
#define GPFSEL0       0          /* word offset in GPIO block */
#define GPIO7_SHIFT   21
#define GPIO7_MASK    (7u << GPIO7_SHIFT)
#define GPIO7_ALT0    (4u << GPIO7_SHIFT)  /* ALT0 = SPI0_CE1_N */

static volatile uint32_t *spi;
static volatile uint32_t *gpio;

static inline uint32_t rds(int r)            { return spi[r]; }
static inline void      wrs(int r, uint32_t v) { spi[r] = v;   }

/* Restore GPIO7 to ALT0 so CE1_N is physically asserted during CS1 transfers.
 * The ads7846 kernel driver reverts GPIO7 to input mode when unbound; without
 * ALT0 the SPI CE1_N pin stays high and the ADS7846 is never selected. */
static void gpio7_alt0(void)
{
    uint32_t v = gpio[GPFSEL0];
    v = (v & ~GPIO7_MASK) | GPIO7_ALT0;
    gpio[GPFSEL0] = v;
}

/* Busy-wait until fbcp finishes the current frame (TA goes 1 → 0).
 * Returns 1 if a 1→0 transition was seen, 0 if fbcp wasn't detected. */
static int wait_interframe(void)
{
    int ta_seen = 0;
    for (long i = 0; i < 5000000L; i++) {
        if (rds(SPI_CS) & CS_TA) { ta_seen = 1; break; }
    }
    if (ta_seen) {
        for (long i = 0; i < 50000000L; i++) {
            if (!(rds(SPI_CS) & CS_TA)) break;
        }
    }
    return ta_seen;
}

/* Send 3-byte ADS7846 command on CS1, return 12-bit result and raw bytes. */
static int ads_read(uint8_t cmd, uint8_t raw_out[3])
{
    uint8_t rx[3] = {0, 0, 0};
    uint8_t tx[3] = {cmd, 0, 0};

    wrs(SPI_CLK, 250);                 /* 250 MHz ÷ 250 = 1 MHz */
    wrs(SPI_CS,  CS_CS1 | CS_CLR);    /* select CS1, clear FIFOs */
    wrs(SPI_CS,  CS_CS1 | CS_TA);     /* start transfer */

    for (int i = 0; i < 3; i++) {
        while (!(rds(SPI_CS) & CS_TXD));
        wrs(SPI_FIFO, tx[i]);
    }
    while (!(rds(SPI_CS) & CS_DONE));
    for (int i = 0; i < 3; i++) {
        while (!(rds(SPI_CS) & CS_RXD));
        rx[i] = (uint8_t)rds(SPI_FIFO);
    }
    wrs(SPI_CS, CS_CS1);              /* deassert TA */

    if (raw_out) { raw_out[0] = rx[0]; raw_out[1] = rx[1]; raw_out[2] = rx[2]; }
    return (rx[1] << 4) | (rx[2] >> 4);
}

int main(void)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) {
        fputs("ta_seen=? raw - - 0\n", stderr);
        puts("err"); return 1;
    }

    void *sp = mmap(NULL, PAGE_SIZE, PROT_READ|PROT_WRITE, MAP_SHARED, fd, SPI0_PHYS);
    void *gp = mmap(NULL, PAGE_SIZE, PROT_READ|PROT_WRITE, MAP_SHARED, fd, GPIO_PHYS);
    close(fd);

    if (sp == MAP_FAILED || gp == MAP_FAILED) {
        fputs("ta_seen=? raw - - 0\n", stderr);
        puts("err"); return 1;
    }
    spi  = (volatile uint32_t *)sp;
    gpio = (volatile uint32_t *)gp;

    /* Ensure GPIO7 (CE1_N) is in ALT0 (SPI0_CE1_N) — may have reverted to
     * input mode when the ads7846 kernel driver was unbound. */
    gpio7_alt0();

    /* Sync to inter-frame gap (or proceed immediately if fbcp not running). */
    int ta_seen = wait_interframe();

    /* Save fbcp's SPI clock divider so we can restore it.
     * If we leave CLK=250 (1 MHz) after our read, subsequent fbcp frames
     * transmit slowly and the display scans. */
    uint32_t saved_clk = rds(SPI_CLK);

    /* ── Hot path: read all samples with NO I/O syscalls ──────────────────
     * All results go into stack arrays; fprintf only after SPI is released. */
    int    raw_ry[4], raw_rx[4];
    uint8_t by[4][3], bx[4][3];   /* raw bytes for diagnosis */

    long sum_y = 0, sum_x = 0;
    int  n = 0;
    for (int i = 0; i < 4; i++) {
        raw_ry[i] = ads_read(CMD_Y, by[i]);
        raw_rx[i] = ads_read(CMD_X, bx[i]);
        if (raw_ry[i] > RAW_MIN && raw_ry[i] < RAW_MAX &&
            raw_rx[i] > RAW_MIN && raw_rx[i] < RAW_MAX) {
            sum_y += raw_ry[i];
            sum_x += raw_rx[i];
            n++;
        }
    }

    /* Restore SPI state immediately — fbcp will reassert CS0 on next frame */
    wrs(SPI_CLK, saved_clk);
    wrs(SPI_CS,  0);
    /* ── End hot path ──────────────────────────────────────────────────── */

    /* Now safe to print diagnostics */
    fprintf(stderr, "ta_seen=%d\n", ta_seen);
    for (int i = 0; i < 4; i++) {
        int ok = (raw_ry[i] > RAW_MIN && raw_ry[i] < RAW_MAX &&
                  raw_rx[i] > RAW_MIN && raw_rx[i] < RAW_MAX);
        fprintf(stderr,
                "sample%d  ry=%4d(bytes %02x %02x %02x)  "
                "rx=%4d(bytes %02x %02x %02x)  %s\n",
                i,
                raw_ry[i], by[i][0], by[i][1], by[i][2],
                raw_rx[i], bx[i][0], bx[i][1], bx[i][2],
                ok ? "ok" : "filtered");
    }

    if (n == 0) {
        fputs("raw - - 0\n", stderr);
        puts("err"); return 1;
    }

    double ay = (double)sum_y / n;
    double ax = (double)sum_x / n;
    fprintf(stderr, "raw %.0f %.0f %d\n", ay, ax, n);

    /* physical Y (CMD_Y) → screen X */
    int sx = (int)((ay - CAL_Y_MIN) / (double)(CAL_Y_MAX - CAL_Y_MIN) * SCREEN_W + 0.5);
    /* physical X (CMD_X) → screen Y (inverted — CAL_X_MAX < CAL_X_MIN) */
    int sy = (int)((ax - CAL_X_MIN) / (double)(CAL_X_MAX - CAL_X_MIN) * SCREEN_H + 0.5);

    if (sx < 0) sx = 0; if (sx >= SCREEN_W) sx = SCREEN_W - 1;
    if (sy < 0) sy = 0; if (sy >= SCREEN_H) sy = SCREEN_H - 1;

    printf("%d %d\n", sx, sy);
    return 0;
}
