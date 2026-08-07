#ifndef DSP_DEVICE_HEADER_FILE_GUARD
#define DSP_DEVICE_HEADER_FILE_GUARD
namespace basic
{
    namespace dsp
    {
        class DeviceInfo
        {
        public:
            virtual double eegFs() = 0;          //脑电数据采样率
            virtual double eegVoltageGain() = 0; //脑电电压放大倍数
            virtual int eegMinVal() = 0;        //脑电数据最小值（原始数据）
            virtual int eegMaxVal() = 0;        //脑电数据最大值（原始数据）
            virtual double eegMaxUv() = 0;       //脑电数据最大值（单位：微伏）
            virtual double eegMinUv() = 0;       //脑电数据最小值（单位：微伏）
            virtual int eegDigits() = 0;        //脑电数据位数
            virtual double hrFs() = 0;           //心率数据采样率
            virtual double peFs() = 0;           //压电数据采样率
            virtual int peMinVal() = 0;          // 压电数据最小值（原始数据）
            virtual int peMaxVal() = 0;          // 压电数据最大值（原始数据）
            virtual double peMinMv() = 0;        // 压电数据最小值（单位：毫伏）
            virtual double peMaxMv() = 0;        // 压电数据最大值（单位：毫伏）
            virtual double prFs() = 0;           //压阻数据
        };

        class DeviceInfoFtV1 : public DeviceInfo
        {
        public:
            double eegFs() override;
            double eegVoltageGain() override;
            int eegMinVal() override;
            int eegMaxVal() override;
            double eegMaxUv() override;
            double eegMinUv() override;
            int eegDigits() override;
            double hrFs() override;
            double peFs() override;
            int peMinVal() override;
            int peMaxVal() override;
            double peMinMv() override;
            double peMaxMv() override;
            double prFs() override;
        };

        class DeviceInfoEyeShade : public DeviceInfo
        {
        public:
            double eegFs() override;
            double eegVoltageGain() override;
            int eegMinVal() override;
            int eegMaxVal() override;
            double eegMaxUv() override;
            double eegMinUv() override;
            int eegDigits() override;
            double hrFs() override;
            double peFs() override;
            int peMinVal() override;
            int peMaxVal() override;
            double peMinMv() override;
            double peMaxMv() override;
            double prFs() override;
        };

        class DeviceInfoCushion : public DeviceInfo
        {
        public:
            double eegFs() override;
            double eegVoltageGain() override;
            int eegMinVal() override;
            int eegMaxVal() override;
            double eegMaxUv() override;
            double eegMinUv() override;
            int eegDigits() override;
            double hrFs() override;
            double peFs() override;
            int peMinVal() override;
            int peMaxVal() override;
            double peMinMv() override;
            double peMaxMv() override;
            double prFs() override;
        };
    } // namespace define

} // namespace basic
#endif