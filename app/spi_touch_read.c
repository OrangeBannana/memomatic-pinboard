/* spi_touch_read.c — ADS7846 coordinate reader via /dev/mem
 *
 * Waits for fbcp-ili9341's DMA to finish a frame (SPI TA: 1→0), then reads
 * one averaged X,Y sample from the ADS7846 in the inter-frame gap.
 * Because the read happens between frames it does not corrupt the display.
 *
 * Outputs "X Y\n"  (xdotool screen coordinates, X11 480×320)
 * or      "err\n"  on failure (caller falls back to a fixed position).
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

/* BCM2835/2710 SPI0 hardware — Pi peripheral base 0x3F000000 */
#define SPI0_PHYS  0x3F204000UL
#define PAGE_SIZE  4096

/* SPI0 register word offsets (each is 4 bytes) */
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

/* ADS7846: 12-bit, differential, PENIRQ re-enabled after each conversion */
#define CMD_Y  0x90   /* channel 1 = physical Y axis of panel */
#define CMD_X  0xD0   /* channel 5 = physical X axis of panel */

/* Calibration constants */
#define CAL_Y_MIN   268
#define CAL_Y_MAX  3880
#define CAL_X_MIN  3936
#define CAL_X_MAX   227   /* note: < MIN — axis is inverted */
#define SCREEN_W    480
#define SCREEN_H    320

static volatile uint32_t *spi;

static inline uint32_t rd(int r)           { return spi[r]; }
static inline void     wr(int r, uint32_t v) { spi[r] = v;   }

/* Busy-wait until fbcp finishes the current frame (TA goes 1 → 0).
 * If TA is never seen high (fbcp idle/not running), returns immediately. */
static void wait_interframe(void)
{
    /* Wait for TA=1 (frame in progress) — skip if never seen */
    for (long i = 0; i < 5000000L; i++) {
        if (rd(SPI_CS) & CS_TA) break;
    }
    /* Wait for TA=0 (frame finished — we are now in the inter-frame gap) */
    for (long i = 0; i < 50000000L; i++) {
        if (!(rd(SPI_CS) & CS_TA)) break;
    }
}

/* Send a 3-byte ADS7846 command on CS1 and return the 12-bit result. */
static int ads_read(uint8_t cmd)
{
    uint8_t rx[3] = {0, 0, 0};
    uint8_t tx[3] = {cmd, 0, 0};

    wr(SPI_CLK, 250);                  /* 250 MHz ÷ 250 = 1 MHz */
    wr(SPI_CS,  CS_CS1 | CS_CLR);     /* select CS1, clear FIFOs */
    wr(SPI_CS,  CS_CS1 | CS_TA);      /* start transfer */

    for (int i = 0; i < 3; i++) {
        while (!(rd(SPI_CS) & CS_TXD));
        wr(SPI_FIFO, tx[i]);
    }
    while (!(rd(SPI_CS) & CS_DONE));
    for (int i = 0; i < 3; i++) {
        while (!(rd(SPI_CS) & CS_RXD));
        rx[i] = (uint8_t)rd(SPI_FIFO);
    }

    wr(SPI_CS, CS_CS1);               /* deassert TA */
    return (rx[1] << 4) | (rx[2] >> 4);
}

int main(void)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { puts("err"); return 1; }

    void *p = mmap(NULL, PAGE_SIZE, PROT_READ | PROT_WRITE,
                   MAP_SHARED, fd, SPI0_PHYS);
    close(fd);
    if (p == MAP_FAILED) { puts("err"); return 1; }
    spi = (volatile uint32_t *)p;

    /* Sync to inter-frame gap, then take up to 4 averaged samples.
     * Save SPI_CLK first: fbcp sets it once at init (typically CDIV=4 for
     * 62.5 MHz) and never resets it per frame.  If we leave CLK=250 (1 MHz)
     * after our read, every subsequent fbcp frame transmits at 1 MHz and the
     * display scans slowly.  Restore it before returning. */
    wait_interframe();

    uint32_t saved_clk = rd(SPI_CLK);

    long sum_y = 0, sum_x = 0;
    int  n = 0;
    for (int i = 0; i < 4; i++) {
        int ry = ads_read(CMD_Y);  /* physical Y → screen X */
        int rx = ads_read(CMD_X);  /* physical X → screen Y */
        if (ry > 50 && ry < 4050 && rx > 50 && rx < 4050) {
            sum_y += ry;
            sum_x += rx;
            n++;
        }
    }

    /* Restore fbcp's SPI clock divider unconditionally */
    wr(SPI_CLK, saved_clk);
    wr(SPI_CS,  0);   /* deassert everything; fbcp will reassert CS0 on next frame */

    if (n == 0) { puts("err"); return 1; }

    double ay = (double)sum_y / n;
    double ax = (double)sum_x / n;

    /* physical Y (CMD_Y) → screen X: range CAL_Y_MIN..CAL_Y_MAX → 0..SCREEN_W */
    int sx = (int)((ay - CAL_Y_MIN) / (double)(CAL_Y_MAX - CAL_Y_MIN) * SCREEN_W + 0.5);
    /* physical X (CMD_X) → screen Y: range CAL_X_MIN..CAL_X_MAX → 0..SCREEN_H
     * (CAL_X_MAX < CAL_X_MIN so the axis is naturally inverted) */
    int sy = (int)((ax - CAL_X_MIN) / (double)(CAL_X_MAX - CAL_X_MIN) * SCREEN_H + 0.5);

    if (sx < 0) sx = 0; if (sx >= SCREEN_W) sx = SCREEN_W - 1;
    if (sy < 0) sy = 0; if (sy >= SCREEN_H) sy = SCREEN_H - 1;

    printf("%d %d\n", sx, sy);
    return 0;
}
