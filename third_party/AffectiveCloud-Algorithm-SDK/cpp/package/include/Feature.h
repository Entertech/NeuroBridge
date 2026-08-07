#ifndef E95AEF56_B457_481E_9483_B4DEF6D11FF4
#define E95AEF56_B457_481E_9483_B4DEF6D11FF4
#include "TypeDefine.h"
#include "Device.h"
#include "Data.h"
#include <vector>
namespace basic::dsp
{
    struct SleepEEGFeaturesCal {
        vectord eegFeature;
        vectord eegTypeFeature;
        EEGPower eegPower;
        EEGQuality quality;
    };
    /**
     * @brief 脑电频谱特征
     *
     * @param eegSplitData 脑电数据片段
     * @param nfft FFT点数
     * @param deviceInfo 设备信息
     * @return vectord
     */
    vectord generalSpectrum(const vectord &eegSplitData, int nfft, DeviceInfo *deviceInfo);

    /**
     * @brief 睡眠频段能量比例特征
     *
     * @param eegSplitData 脑电数据片段
     * @param nfft FFT点数
     * @param deviceInfo 设备信息
     * @return vectord
     */
    vectord sleepPowerRate(const vectord &eegSplitData, int nfft, DeviceInfo *deviceInfo);

    /**
     * 睡眠脑电特征
     * @param eegSplitData 脑电数据片段
     * @param nfft FFT点数
     * @param deviceInfo 设备信息
     * @return
     */
    SleepEEGFeaturesCal sleepEEGFeatures(const vectord &eegSplitData, int nfft, DeviceInfo *deviceInfo);

    /// @brief 冥想脑电特征
    /// @param eeglSplitData 左通道脑电数据片段
    /// @param eegrSplitData 右通道脑电数据片段
    /// @param deviceInfo 设备信息
    /// @return 频谱幅值
    vectord meditationEEGFeatures(const vectord &eeglSplitData, const vectord &eegrSplitData, DeviceInfo *deviceInfo);
}
    

#endif /* E95AEF56_B457_481E_9483_B4DEF6D11FF4 */
