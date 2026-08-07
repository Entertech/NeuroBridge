#ifndef D4AEE075_C3CC_4FD9_BC59_4166F32759FC
#define D4AEE075_C3CC_4FD9_BC59_4166F32759FC
#include "Device.h"
#include "TypeDefine.h"


namespace basic
{
    namespace dsp
    {
        /**
         * @brief 计算心跳间期
         * 
         * @param hrSeq 心率值序列
         * @return vectord 心跳间期序列（单位：毫秒）
         */
        vectord hrIntervalCal(const vectord &hrSeq);

        /**
         * @brief 计算心率变异性
         * 
         * @param hrIntervalSeq 心跳间期暂存序列
         * @param seqLim 计算心率变异性所需心跳间期序列时长下限（单位：秒）
         * @param deviceInfo 设备信息
         * @return double 心率变异性（单位：毫秒）
         */
        double hrvValCal(const vectord &hrIntervalSeq, double seqLim, DeviceInfo *deviceInfo);

        /**
         * @brief 计算心率变异性序列（序列时长需大于下限时，输出心率变异性序列，长度与心率值序列的增量相同）
         * 
         * @param hrSeq 心率值暂存序列
         * @param seqLim 计算心率变异性所需心率值序列时长下限（单位：秒）
         * @param deviceInfo 设备信息
         * @return vectord 心率变异性序列（单位：毫秒）
         */
        vectord hrSeqCal(const vectord &hrSeq, double seqLim, DeviceInfo *deviceInfo);

        /**
         * @brief 计算心率变异性频段能量
         * 
         * @param hrvSeq 心率变异性序列
         * @param freqLowLim 下限截止频率
         * @param freqHighLim 上限截止频率
         * @param fs 心率变异性序列的采样率
         * @param nfft FFT点数
         * @return float 
         */
        double hrvPowerCal(const vectord &hrvSeq, double freqLowLim, double freqHighLim, double fs);


        /// 有效性校验
        /// \param nnIntervalData 心率间期数据
        /// \return 心率统计指标: 幅度 众数 跨度
        std::tuple<double, double, double> hrStatMetricsCal(const std::vector<double>& nnIntervalData);
    }
}
#endif /* D4AEE075_C3CC_4FD9_BC59_4166F32759FC */
